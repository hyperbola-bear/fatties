"""A weight-loss plan: current and target weight in, daily targets out.

Energy needs use Mifflin-St Jeor at the chosen exercise level. The daily
deficit comes from the pace (about 7,700 kcal per kg of body fat), either
chosen directly or worked out from a target date. Protein is set per kg of
target weight, fat is a share of calories, carbs take the rest, and sugar is
a cap inside carbs (WHO: under 10 percent of calories).

Two safety limits: pace is capped at 1 percent of body weight a week, and
calories never go below 1,500 (men) or 1,200 (women). When either limit
applies, the plan says so and moves the finish date instead.

These are starting points to adjust, not medical advice.
"""

from __future__ import annotations

import math
from datetime import date, timedelta
from typing import Any

ACTIVITY = {"sedentary": 1.2, "light": 1.375, "moderate": 1.55, "active": 1.725}
TARGET_KEYS = ("kcal", "carbs_g", "protein_g", "fat_g", "sugar_g")
KCAL_PER_KG = 7700
MAX_WEEKLY_SHARE = 0.01
PROTEIN_G_PER_KG = 1.8
FAT_SHARE = 0.275
SUGAR_SHARE = 0.10


class PlanError(ValueError):
    pass


def maintenance_kcal(*, weight_kg: float, height_cm: float, age: int, sex: str, activity: str) -> float:
    if sex not in ("male", "female"):
        raise PlanError("sex must be male or female")
    if activity not in ACTIVITY:
        raise PlanError(f"exercise must be one of {', '.join(ACTIVITY)}")
    bmr = 10 * weight_kg + 6.25 * height_cm - 5 * age + (5 if sex == "male" else -161)
    return bmr * ACTIVITY[activity]


def split_calories(kcal: float, *, protein_g: float) -> dict[str, float]:
    fat_g = kcal * FAT_SHARE / 9
    carbs_g = max(0.0, (kcal - protein_g * 4 - fat_g * 9) / 4)
    sugar_g = min(kcal * SUGAR_SHARE / 4, carbs_g)
    return {
        "kcal": round(kcal),
        "carbs_g": round(carbs_g),
        "protein_g": round(protein_g),
        "fat_g": round(fat_g),
        "sugar_g": round(sugar_g),
    }


def build_plan(
    *,
    weight_kg: float,
    target_weight_kg: float,
    height_cm: float,
    age: int,
    sex: str,
    activity: str = "moderate",
    pace_kg_per_week: float = 0.5,
    target_date: date | None = None,
    start: date,
) -> dict[str, Any]:
    """Return {"targets": daily targets, "plan": how they were reached and when the goal lands}."""
    if target_weight_kg >= weight_kg:
        raise PlanError("target weight must be below current weight")
    if target_date is not None and target_date <= start:
        raise PlanError("the target date must be after today")
    maintenance = maintenance_kcal(weight_kg=weight_kg, height_cm=height_cm, age=age, sex=sex, activity=activity)
    to_lose = weight_kg - target_weight_kg
    safe_pace = round(weight_kg * MAX_WEEKLY_SHARE, 2)
    notes: list[str] = []

    if target_date is not None:
        weeks_given = (target_date - start).days / 7
        wanted = to_lose / weeks_given
        if wanted > safe_pace:
            notes.append(
                f"Losing {to_lose:g} kg by {target_date:%-d %b %Y} needs {wanted:.2f} kg a week. "
                f"The safe limit for you is about {safe_pace:g} kg a week, so the plan uses that."
            )
            pace = safe_pace
        else:
            pace = wanted
    else:
        pace = pace_kg_per_week
        if pace > safe_pace:
            notes.append(
                f"{pace:g} kg a week is faster than the safe limit of about {safe_pace:g} kg, so the plan uses that."
            )
            pace = safe_pace
    if pace <= 0:
        raise PlanError("pace must be above 0")

    floor = 1500 if sex == "male" else 1200
    kcal = maintenance - pace * KCAL_PER_KG / 7
    if kcal < floor:
        kcal = floor
        pace = max(0.0, (maintenance - floor) * 7 / KCAL_PER_KG)
        notes.append(
            f"Eating below {floor} kcal a day is not advised without a doctor, so the plan stops there "
            f"and the pace drops to about {pace:.2f} kg a week."
        )
    if pace <= 0:
        raise PlanError(f"maintenance is already near {floor} kcal; talk to a doctor about a plan")

    weeks = to_lose / pace
    finish = start + timedelta(days=math.ceil(weeks * 7))
    if target_date is not None and finish > target_date:
        notes.append(f"At this pace you should reach {target_weight_kg:g} kg around {finish:%-d %b %Y}.")
    notes.append("Maintenance drops as you lose weight, so update the plan with your new weight every few weeks.")

    targets = split_calories(kcal, protein_g=PROTEIN_G_PER_KG * target_weight_kg)
    return {
        "targets": targets,
        "plan": {
            "weight_kg": weight_kg,
            "target_weight_kg": target_weight_kg,
            "height_cm": height_cm,
            "age": age,
            "sex": sex,
            "activity": activity,
            "target_date": target_date.isoformat() if target_date else None,
            "maintenance_kcal": round(maintenance),
            "deficit_kcal": round(maintenance - kcal),
            "pace_kg_per_week": round(pace, 2),
            "weeks": round(weeks, 1),
            "finish_on": finish.isoformat(),
            "notes": notes,
        },
    }
