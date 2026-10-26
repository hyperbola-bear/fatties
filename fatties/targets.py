"""Daily targets: calories first, then the macros split out of them.

Energy needs use Mifflin-St Jeor at a chosen activity level, minus a deficit.
Protein is set per kg of body weight, fat is a share of calories, carbs take
the rest, and sugar is a cap inside carbs (WHO: under 10 percent of calories).
These are starting points to adjust, not medical advice.
"""

from __future__ import annotations

ACTIVITY = {"sedentary": 1.2, "light": 1.375, "moderate": 1.55, "active": 1.725}
TARGET_KEYS = ("kcal", "carbs_g", "protein_g", "fat_g", "sugar_g")

# Used until someone enters their own numbers.
DEFAULT_TARGETS: dict[str, float] = {"kcal": 1800, "carbs_g": 216, "protein_g": 110, "fat_g": 55, "sugar_g": 45}


def suggest_targets(
    *,
    weight_kg: float,
    height_cm: float,
    age: int,
    sex: str,
    activity: str = "moderate",
    deficit_kcal: float = 500,
    protein_g_per_kg: float = 1.8,
    fat_share: float = 0.275,
) -> dict[str, float]:
    if sex not in ("male", "female"):
        raise ValueError("sex must be male or female")
    if activity not in ACTIVITY:
        raise ValueError(f"activity must be one of {', '.join(ACTIVITY)}")
    bmr = 10 * weight_kg + 6.25 * height_cm - 5 * age + (5 if sex == "male" else -161)
    maintenance = bmr * ACTIVITY[activity]
    # Do not suggest eating below these without a clinician.
    floor = 1500 if sex == "male" else 1200
    kcal = max(floor, maintenance - deficit_kcal)
    protein_g = protein_g_per_kg * weight_kg
    fat_g = kcal * fat_share / 9
    carbs_g = max(0.0, (kcal - protein_g * 4 - fat_g * 9) / 4)
    sugar_g = kcal * 0.10 / 4
    return {
        "kcal": round(kcal),
        "carbs_g": round(carbs_g),
        "protein_g": round(protein_g),
        "fat_g": round(fat_g),
        "sugar_g": round(min(sugar_g, carbs_g)),
        "maintenance_kcal": round(maintenance),
    }
