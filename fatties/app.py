"""HTTP API and the phone web app.

Flow: the phone posts a photo to /api/analyze and gets an estimate back
without anything being saved. The person adjusts portions, then posts the
final items to /api/meals. /api/day and /api/week compare against targets.

Browser API routes need a login cookie, except login/logout/session. Two
dedicated read-only widget routes require a scoped bearer credential instead.
The page and static files are public and contain no account data.
"""

from __future__ import annotations

import base64
import binascii
import logging
import threading
import uuid
from datetime import date, datetime
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from fastapi import FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool

from .analysis import MEAL_TYPES, AnalysisError, Analyzer, check_photo, make_analyzer, validate_analysis
from .auth import COOKIE, SESSION_SECONDS, LoginLimiter, Sessions, fingerprint, hash_password, verify_password
from .config import Settings
from .photos import PhotoError, PhotoStorage, make_photo_storage, prepare_photo
from .store import Store
from .summary import day_summary, period_summary, week_summary
from .targets import PlanError, build_plan

STATIC = Path(__file__).with_name("static")
PUBLIC_API = {"/api/login", "/api/logout", "/api/session"}
WIDGET_READ_API = {"/api/widget/partner/latest", "/api/widget/partner/latest/thumbnail"}
logger = logging.getLogger(__name__)
# Checked when the name is unknown, so a wrong name takes as long as a wrong password.
_DUMMY_HASH = hash_password("not-a-real-password")
MAX_NOTE_CHARS = 500


class Login(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1, max_length=200)


class Targets(BaseModel):
    kcal: float = Field(gt=0, le=10000)
    carbs_g: float = Field(ge=0, le=2000)
    protein_g: float = Field(ge=0, le=1000)
    fat_g: float = Field(ge=0, le=1000)
    sugar_g: float = Field(ge=0, le=1000)


class PlanInputs(BaseModel):
    weight_kg: float = Field(gt=30, lt=400)
    target_weight_kg: float = Field(gt=30, lt=400)
    height_cm: float = Field(gt=100, lt=250)
    age: int = Field(ge=16, le=100)
    sex: Literal["male", "female"]
    activity: Literal["sedentary", "light", "moderate", "active"] = "moderate"
    pace_kg_per_week: float = Field(default=0.5, gt=0, le=1.5)
    target_date: date | None = None


class NewPlan(BaseModel):
    inputs: PlanInputs
    # Targets as shown to the person, who may have adjusted them; omitted means use the worked-out ones.
    targets: Targets | None = None


class Item(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)
    name: str = Field(min_length=1, max_length=200)
    local_name: str = ""
    portion: str = ""
    estimated_weight_g: float = Field(ge=0)
    kcal: float = Field(ge=0)
    carbs_g: float = Field(ge=0)
    protein_g: float = Field(ge=0)
    fat_g: float = Field(ge=0)
    sugar_g: float = Field(ge=0)
    confidence: str = "medium"


class NewMeal(BaseModel):
    person_id: int
    eaten_at: datetime | None = None
    meal_type: str | None = None
    title: str = Field(min_length=1, max_length=200)
    note: str | None = Field(default=None, max_length=MAX_NOTE_CHARS)
    items: list[Item] = Field(min_length=1)
    model: str | None = None
    photo_base64: str | None = Field(default=None, max_length=7_000_000)
    request_id: uuid.UUID | None = None


def create_app(settings: Settings, *, analyzer: Analyzer | None = None, store: Store | None = None,
               photo_storage: PhotoStorage | None = None) -> FastAPI:
    tz = ZoneInfo(settings.timezone)
    store = store or Store(settings.db_path)
    store.ensure_people(settings.people)
    photos = photo_storage or make_photo_storage(settings)
    photo_lock = threading.RLock()
    if settings.public_url:
        parsed_url = urlsplit(settings.public_url)
        if parsed_url.scheme != "https" or not parsed_url.netloc or parsed_url.query or parsed_url.fragment or parsed_url.path:
            raise ValueError("FATTIES_PUBLIC_URL must be an HTTPS origin, without a path, query or fragment")

    def cleanup_photos() -> None:
        for key in store.photo_cleanup_keys():
            try:
                photos.delete(key)
                store.finish_photo_cleanup(key)
            except (PhotoError, OSError):
                logger.warning("Private photo cleanup queued for retry")
                break

    with photo_lock:
        cleanup_photos()
    analyzer = analyzer or make_analyzer(
        settings.analyzer, api_key=settings.api_key, model=settings.model, effort=settings.effort
    )
    sessions = Sessions(store.session_secret())
    limiter = LoginLimiter()
    # No API docs: they would list every route to anyone who finds the server.
    app = FastAPI(title="fatties", docs_url=None, redoc_url=None, openapi_url=None)

    def logged_in(request: Request) -> dict | None:
        payload = sessions.read(request.cookies.get(COOKIE))
        if not payload:
            return None
        stored = store.password_hash(payload["pid"])
        # A changed password no longer matches the fingerprint, which signs out old devices.
        if not stored or fingerprint(stored) != payload.get("pw"):
            return None
        return store.person(payload["pid"])

    @app.middleware("http")
    async def require_login(request: Request, call_next):
        path = request.url.path
        widget_read = path in WIDGET_READ_API and request.method == "GET"
        if path.startswith("/api/") and path not in PUBLIC_API and not widget_read and logged_in(request) is None:
            return JSONResponse({"detail": "log in first"}, status_code=401)
        return await call_next(request)

    @app.get("/api/session")
    def session(request: Request) -> dict:
        person = logged_in(request)
        if person is None:
            raise HTTPException(401, {"message": "log in first", "passwords_set": store.anyone_has_password()})
        return {"person": person}

    @app.post("/api/login")
    def login(body: Login, request: Request, response: Response) -> dict:
        key = body.name.strip().lower()
        if limiter.blocked(key):
            raise HTTPException(429, "too many wrong passwords; wait 15 minutes and try again")
        person = store.person_by_name(body.name)
        stored = store.password_hash(person["id"]) if person else None
        if not verify_password(body.password, stored or _DUMMY_HASH) or not stored:
            limiter.failed(key)
            raise HTTPException(401, "wrong name or password")
        limiter.succeeded(key)
        response.set_cookie(
            COOKIE,
            sessions.issue(person["id"], stored),
            max_age=SESSION_SECONDS,
            httponly=True,
            samesite="lax",
            # Behind an HTTPS proxy uvicorn sees https here; plain http stays usable on localhost.
            secure=request.url.scheme == "https",
        )
        return {"person": person}

    @app.post("/api/logout", status_code=204)
    def logout(response: Response, request: Request) -> None:
        person = logged_in(request)
        if person:
            store.revoke_widget_credentials(person["id"])
        response.delete_cookie(COOKIE, httponly=True, samesite="lax")

    def today() -> date:
        return datetime.now(tz).date()

    def parse_day(value: str | None) -> date:
        if not value:
            return today()
        try:
            parsed = date.fromisoformat(value)
            if not 1900 <= parsed.year <= 2100:
                raise ValueError
            return parsed
        except ValueError:
            raise HTTPException(400, "date must be YYYY-MM-DD between 1900 and 2100") from None

    def require_person(person_id: int) -> dict:
        person = store.person(person_id)
        if not person:
            raise HTTPException(404, "no such person")
        return person

    def require_owner(request: Request, person_id: int) -> dict:
        person = logged_in(request)
        if person is None:
            raise HTTPException(401, "log in first")
        if person["id"] != person_id:
            raise HTTPException(403, "you can only change your own plan and meals")
        return person

    @app.get("/api/config")
    def config() -> dict:
        return {"today": today().isoformat(), "meal_types": MEAL_TYPES, "model": analyzer.model}

    @app.get("/api/people")
    def people() -> list:
        return store.people()

    def make_plan(inputs: PlanInputs) -> dict:
        try:
            return build_plan(**inputs.model_dump(), start=today())
        except PlanError as exc:
            raise HTTPException(400, str(exc)) from None

    @app.post("/api/plan/preview")
    def preview_plan(inputs: PlanInputs) -> dict:
        return make_plan(inputs)

    @app.put("/api/people/{person_id}/plan")
    def start_plan(person_id: int, new: NewPlan, request: Request) -> dict:
        """Start a plan today. Earlier days keep the plan that applied to them."""
        require_person(person_id)
        require_owner(request, person_id)
        plan = make_plan(new.inputs)
        targets = plan["targets"]
        details = plan["plan"]
        if new.targets is not None:
            chosen = new.targets.model_dump()
            if chosen["sugar_g"] > chosen["carbs_g"]:
                raise HTTPException(400, "the sugar limit sits inside carbs, so it cannot be larger than carbs")
            if any(round(chosen[key]) != round(targets[key]) for key in targets):
                details = {**details, "worked_out_targets": targets, "adjusted": True}
                targets = chosen
        store.save_plan(person_id, today(), targets, details)
        return store.person(person_id)

    @app.post("/api/analyze")
    async def analyze(
        photo: UploadFile = File(...),
        meal_type: str | None = Form(None),
        note: str | None = Form(None),
    ) -> dict:
        if meal_type and meal_type not in MEAL_TYPES:
            raise HTTPException(400, f"meal_type must be one of {', '.join(MEAL_TYPES)}")
        note = (note or "").strip()[:MAX_NOTE_CHARS] or None
        try:
            checked = check_photo(photo.content_type or "", await photo.read(5 * 1024 * 1024 + 1))
            # The SDK call blocks, so it runs off the event loop.
            return await run_in_threadpool(analyzer.analyze, checked, meal_type=meal_type or None, note=note)
        except AnalysisError as exc:
            raise HTTPException(exc.status, {"message": str(exc), "retryable": exc.retryable}) from None

    @app.post("/api/meals", status_code=201)
    def add_meal(meal: NewMeal, request: Request) -> dict:
        require_person(meal.person_id)
        require_owner(request, meal.person_id)
        if not meal.title.strip():
            raise HTTPException(400, "give the meal a name")
        if meal.meal_type and meal.meal_type not in MEAL_TYPES:
            raise HTTPException(400, f"meal_type must be one of {', '.join(MEAL_TYPES)}")
        # Re-run the same cleaning as a model answer, so edited items obey the same rules.
        cleaned = validate_analysis({"is_food": True, "items": [item.model_dump() for item in meal.items]}, fill_missing_calories=False)
        eaten_at = meal.eaten_at or datetime.now(tz)
        eaten_at = eaten_at.astimezone(tz) if eaten_at.tzinfo else eaten_at.replace(tzinfo=tz)
        request_id = str(meal.request_id) if meal.request_id else None
        with photo_lock:
            if request_id and (previous := store.request_meal(meal.person_id, request_id)):
                if (previous["title"] != meal.title.strip() or previous["items"] != cleaned["items"]
                        or previous["note"] != meal.note or previous["meal_type"] != meal.meal_type
                        or previous["model"] != meal.model
                        or (meal.eaten_at is not None and previous["eaten_at"] != eaten_at.isoformat(timespec="minutes"))):
                    raise HTTPException(409, "this save already completed with different details; open Track to review it before logging again")
                return previous
            key = None
            if meal.photo_base64:
                try:
                    image, thumbnail = prepare_photo(base64.b64decode(meal.photo_base64, validate=True))
                except (PhotoError, binascii.Error, ValueError) as exc:
                    raise HTTPException(400, str(exc) if isinstance(exc, PhotoError) else "invalid photo encoding") from None
                key = uuid.uuid4().hex
                store.schedule_photo_cleanup(key)
                try:
                    photos.put(key, image, thumbnail)
                except (PhotoError, OSError):
                    cleanup_photos()
                    raise HTTPException(503, "photo storage is unavailable; your meal has not been saved. Please retry") from None
            try:
                saved = store.add_meal(
                    person_id=meal.person_id, eaten_at=eaten_at, meal_type=meal.meal_type,
                    title=meal.title.strip(), note=meal.note, items=cleaned["items"], model=meal.model,
                    photo_key=key, request_id=request_id,
                )
            except Exception:
                cleanup_photos()
                raise
            cleanup_photos()
            return saved

    @app.delete("/api/meals/{meal_id}", status_code=204)
    def delete_meal(meal_id: int, request: Request) -> None:
        with photo_lock:
            meal = store.meal(meal_id)
            if meal is None:
                raise HTTPException(404, "no such meal")
            require_owner(request, meal["person_id"])
            store.delete_meal(meal_id)
            cleanup_photos()

    @app.get("/api/meals/{meal_id}")
    def get_meal(meal_id: int) -> dict:
        meal = store.meal(meal_id)
        if meal is None:
            raise HTTPException(404, "this meal has been deleted or does not exist")
        return {**meal, "person": store.person(meal["person_id"])}

    def photo_response(meal_id: int, thumbnail: bool) -> Response:
        key = store.photo_key(meal_id)
        if not key:
            raise HTTPException(404, "no photo for this meal")
        try:
            return Response(photos.get(key, thumbnail=thumbnail), media_type="image/jpeg")
        except PhotoError:
            raise HTTPException(503, "photo temporarily unavailable") from None

    @app.get("/api/meals/{meal_id}/photo")
    def meal_photo(meal_id: int, thumbnail: bool = False) -> Response:
        return photo_response(meal_id, thumbnail)

    @app.get("/api/day")
    def day(date: str | None = None) -> dict:
        return day_summary(store, parse_day(date))

    @app.get("/api/week")
    def week(date: str | None = None) -> dict:
        return week_summary(store, parse_day(date), today())

    @app.get("/api/track")
    def track(period: Literal["day", "week", "month", "year"] = "day", date: str | None = None) -> dict:
        selected = parse_day(date)
        if period == "day":
            return {"period": period, **day_summary(store, selected)}
        return period_summary(store, selected, today(), period)

    def origin(request: Request) -> str:
        return settings.public_url or str(request.base_url).rstrip("/")

    def widget_user(request: Request) -> dict:
        scheme, _, token = request.headers.get("authorization", "").partition(" ")
        person = store.widget_person(token) if scheme.lower() == "bearer" else None
        if person is None:
            raise HTTPException(401, "widget connection expired or disconnected; reconnect in Fatties")
        return person

    def widget_partner(person: dict) -> dict:
        others = [p for p in store.people() if p["id"] != person["id"]]
        if len(others) != 1:
            raise HTTPException(409, "widget requires exactly two people")
        return others[0]

    @app.get("/api/widget/credentials")
    def widget_connections(request: Request) -> dict:
        person = logged_in(request)
        return {"connections": store.widget_connections(person["id"])}

    @app.post("/api/widget/credentials", status_code=201)
    def connect_widget(request: Request) -> dict:
        if request.url.scheme != "https" and request.url.hostname not in {"localhost", "127.0.0.1", "::1"}:
            raise HTTPException(400, "open Fatties over HTTPS to connect your widget")
        person = logged_in(request)
        partner = widget_partner(person)
        token, expires_at = store.issue_widget_credential(person["id"])
        return {"token": token, "expires_at": expires_at, "origin": origin(request),
                "account_id": person["id"], "account_name": person["name"], "partner_name": partner["name"]}

    @app.delete("/api/widget/credentials", status_code=204)
    def disconnect_widget(request: Request) -> None:
        store.revoke_widget_credentials(logged_in(request)["id"])

    @app.get("/api/widget/partner/latest")
    def widget_latest(request: Request) -> dict:
        partner = widget_partner(widget_user(request))
        meal = store.latest_meal(partner["id"])
        result = None if meal is None else {
            "id": meal["id"], "title": meal["title"], "kcal": meal["totals"]["kcal"],
            "logged_at": meal["created_at"], "eaten_at": meal["eaten_at"], "has_photo": meal["has_photo"],
            "url": f"{origin(request)}/?meal={meal['id']}",
            "thumbnail_url": f"{origin(request)}/api/widget/partner/latest/thumbnail?meal_id={meal['id']}" if meal["has_photo"] else None,
        }
        return {"partner": {"id": partner["id"], "name": partner["name"]}, "meal": result,
                "updated_at": datetime.now(tz).isoformat()}

    @app.get("/api/widget/partner/latest/thumbnail")
    def widget_thumbnail(request: Request, meal_id: int) -> Response:
        partner = widget_partner(widget_user(request))
        with photo_lock:
            latest = store.latest_meal(partner["id"])
            if latest is None or latest["id"] != meal_id:
                raise HTTPException(409, "latest meal changed; refresh the widget data")
            return photo_response(meal_id, True)

    @app.get("/widgets/Fatties.js")
    def widget_script() -> FileResponse:
        return FileResponse(STATIC / "Fatties.js", media_type="text/javascript",
                            filename="Fatties.js", headers={"Cache-Control": "no-cache"})

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})

    @app.middleware("http")
    async def revalidate_static(request, call_next):
        # Phones keep home-screen apps cached for a long time; make them check for updates.
        response = await call_next(request)
        if request.url.path.startswith("/static/"):
            response.headers["Cache-Control"] = "no-cache"
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
            response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app


def set_password(store: Store, name: str, password: str) -> dict:
    person = store.person_by_name(name)
    if person is None:
        names = ", ".join(p["name"] for p in store.people())
        raise ValueError(f"no one called {name!r}; the people are {names} (set them with FATTIES_PEOPLE)")
    store.set_password_hash(person["id"], hash_password(password))
    return person


def set_password_command(argv: list[str]) -> None:
    import argparse
    import getpass

    parser = argparse.ArgumentParser(prog="fatties set-password", description="Set or change a login password.")
    parser.add_argument("name", help="the person, as named in FATTIES_PEOPLE")
    args = parser.parse_args(argv)
    settings = Settings.load()
    store = Store(settings.db_path)
    store.ensure_people(settings.people)
    password = getpass.getpass(f"New password for {args.name}: ")
    if getpass.getpass("Again: ") != password:
        raise SystemExit("The two passwords do not match.")
    try:
        person = set_password(store, args.name, password)
    except ValueError as exc:
        raise SystemExit(str(exc)) from None
    print(f"Password set for {person['name']}. Devices signed in with an old password are signed out.")


def main(argv: list[str] | None = None) -> None:
    import argparse
    import sys

    import uvicorn

    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["set-password"]:
        return set_password_command(argv[1:])
    if argv[:1] == ["migrate-photos-to-r2"]:
        from .photo_migration import migrate_photos_command

        return migrate_photos_command(argv[1:])
    parser = argparse.ArgumentParser(
        description="Run the fatties server. Use 'fatties set-password NAME' to set a login password."
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args(argv)
    settings = Settings.load()
    store = Store(settings.db_path)
    if not store.anyone_has_password():
        names = " and ".join(settings.people)
        print(f"No passwords are set, so nobody can log in. Run 'fatties set-password NAME' for {names}.")
    uvicorn.run(create_app(settings, store=store), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
