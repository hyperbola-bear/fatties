"""HTTP API and the phone web app.

Flow: the phone posts a photo to /api/analyze and gets an estimate back
without anything being saved. The person adjusts portions, then posts the
final items to /api/meals. /api/day and /api/week compare against targets.

Every /api route except login, logout and the session check needs a login
cookie. The page and its static files are public; they hold no data.
"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

from fastapi import FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from .analysis import MEAL_TYPES, AnalysisError, Analyzer, check_photo, make_analyzer, validate_analysis
from .auth import COOKIE, SESSION_SECONDS, LoginLimiter, Sessions, fingerprint, hash_password, verify_password
from .config import Settings
from .store import Store
from .summary import day_summary, week_summary
from .targets import PlanError, build_plan

STATIC = Path(__file__).with_name("static")
PUBLIC_API = {"/api/login", "/api/logout", "/api/session"}
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


def create_app(settings: Settings, *, analyzer: Analyzer | None = None, store: Store | None = None) -> FastAPI:
    tz = ZoneInfo(settings.timezone)
    store = store or Store(settings.db_path)
    store.ensure_people(settings.people)
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
        if path.startswith("/api/") and path not in PUBLIC_API and logged_in(request) is None:
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
    def logout(response: Response) -> None:
        response.delete_cookie(COOKIE, httponly=True, samesite="lax")

    def today() -> date:
        return datetime.now(tz).date()

    def parse_day(value: str | None) -> date:
        if not value:
            return today()
        try:
            return date.fromisoformat(value)
        except ValueError:
            raise HTTPException(400, "date must be YYYY-MM-DD") from None

    def require_person(person_id: int) -> dict:
        person = store.person(person_id)
        if not person:
            raise HTTPException(404, "no such person")
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
    def start_plan(person_id: int, new: NewPlan) -> dict:
        """Start a plan today. Earlier days keep the plan that applied to them."""
        require_person(person_id)
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
            checked = check_photo(photo.content_type or "", await photo.read())
            # The SDK call blocks, so it runs off the event loop.
            return await run_in_threadpool(analyzer.analyze, checked, meal_type=meal_type or None, note=note)
        except AnalysisError as exc:
            raise HTTPException(exc.status, {"message": str(exc), "retryable": exc.retryable}) from None

    @app.post("/api/meals", status_code=201)
    def add_meal(meal: NewMeal) -> dict:
        require_person(meal.person_id)
        if meal.meal_type and meal.meal_type not in MEAL_TYPES:
            raise HTTPException(400, f"meal_type must be one of {', '.join(MEAL_TYPES)}")
        # Re-run the same cleaning as a model answer, so edited items obey the same rules.
        cleaned = validate_analysis({"is_food": True, "items": [item.model_dump() for item in meal.items]})
        eaten_at = meal.eaten_at or datetime.now(tz)
        eaten_at = eaten_at.astimezone(tz) if eaten_at.tzinfo else eaten_at.replace(tzinfo=tz)
        return store.add_meal(
            person_id=meal.person_id,
            eaten_at=eaten_at,
            meal_type=meal.meal_type,
            title=meal.title.strip(),
            note=meal.note,
            items=cleaned["items"],
            model=meal.model,
        )

    @app.delete("/api/meals/{meal_id}", status_code=204)
    def delete_meal(meal_id: int) -> None:
        if not store.delete_meal(meal_id):
            raise HTTPException(404, "no such meal")

    @app.get("/api/day")
    def day(date: str | None = None) -> dict:
        return day_summary(store, parse_day(date))

    @app.get("/api/week")
    def week(date: str | None = None) -> dict:
        return week_summary(store, parse_day(date), today())

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})

    @app.middleware("http")
    async def revalidate_static(request, call_next):
        # Phones keep home-screen apps cached for a long time; make them check for updates.
        response = await call_next(request)
        if request.url.path.startswith("/static/"):
            response.headers["Cache-Control"] = "no-cache"
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
