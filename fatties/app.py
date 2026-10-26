"""HTTP API and the phone web app.

Flow: the phone posts a photo to /api/analyze and gets an estimate back
without anything being saved. The person adjusts portions, then posts the
final items to /api/meals. /api/day and /api/week compare against targets.
"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from .analysis import MEAL_TYPES, AnalysisError, Analyzer, check_photo, make_analyzer, validate_analysis
from .config import Settings
from .store import Store
from .summary import day_summary, week_summary
from .targets import suggest_targets

STATIC = Path(__file__).with_name("static")
MAX_NOTE_CHARS = 500


class Targets(BaseModel):
    kcal: float = Field(gt=0, le=10000)
    carbs_g: float = Field(ge=0, le=2000)
    protein_g: float = Field(ge=0, le=1000)
    fat_g: float = Field(ge=0, le=1000)
    sugar_g: float = Field(ge=0, le=1000)


class TargetInputs(BaseModel):
    weight_kg: float = Field(gt=20, lt=400)
    height_cm: float = Field(gt=100, lt=250)
    age: int = Field(ge=16, le=100)
    sex: Literal["male", "female"]
    activity: Literal["sedentary", "light", "moderate", "active"] = "moderate"
    deficit_kcal: float = Field(default=500, ge=0, le=1000)


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
    app = FastAPI(title="fatties")

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

    @app.put("/api/people/{person_id}/targets")
    def put_targets(person_id: int, targets: Targets) -> dict:
        require_person(person_id)
        if targets.sugar_g > targets.carbs_g:
            raise HTTPException(400, "the sugar limit sits inside carbs, so it cannot be larger than carbs")
        store.set_targets(person_id, targets.model_dump())
        return store.person(person_id)

    @app.post("/api/targets/suggest")
    def suggest(inputs: TargetInputs) -> dict:
        return suggest_targets(**inputs.model_dump())

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


def main() -> None:
    import argparse

    import uvicorn

    parser = argparse.ArgumentParser(description="Run the fatties server.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    uvicorn.run(create_app(Settings.load()), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
