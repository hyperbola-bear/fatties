"""Daily and weekly comparisons of what each person ate against their targets.

Weeks run Monday to Sunday. Averages are over days with at least one logged
meal, so a day nobody logged does not look like a perfect zero-calorie day.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from .analysis import NUTRIENTS
from .store import Store

_EMPTY = {key: 0.0 for key in NUTRIENTS}


def _diff(consumed: dict[str, float], target: dict[str, float], days: int = 1) -> dict[str, float]:
    return {key: round(target[key] * days - consumed[key], 1) for key in NUTRIENTS}


def _percent(consumed: dict[str, float], target: dict[str, float], days: int = 1) -> dict[str, int]:
    return {key: round(100 * consumed[key] / (target[key] * days)) if target[key] and days else 0 for key in NUTRIENTS}


def _sum(days: list[dict[str, float]]) -> dict[str, float]:
    return {key: round(sum(day[key] for day in days), 1) for key in NUTRIENTS}


def _average(days: list[dict[str, float]]) -> dict[str, float]:
    if not days:
        return dict(_EMPTY)
    return {key: round(sum(day[key] for day in days) / len(days), 1) for key in NUTRIENTS}


def day_summary(store: Store, day: date) -> dict[str, Any]:
    people = []
    for person in store.people():
        meals = store.meals_on(person["id"], day)
        consumed = _sum([meal["totals"] for meal in meals]) if meals else dict(_EMPTY)
        target = person["targets"]
        people.append(
            {
                "person": person,
                "consumed": consumed,
                "target": target,
                "remaining": _diff(consumed, target),
                "percent": _percent(consumed, target),
                "meals": meals,
            }
        )
    return {"date": day.isoformat(), "people": people}


def week_start(day: date) -> date:
    return day - timedelta(days=day.weekday())


def week_summary(store: Store, day: date, today: date) -> dict[str, Any]:
    start = week_start(day)
    end = start + timedelta(days=6)
    prev_start = start - timedelta(days=7)
    # Days of this week that have begun: all 7 for a past week, none for a future one.
    elapsed = max(0, min(7, (today - start).days + 1))
    people = []
    for person in store.people():
        target = person["targets"]
        logged = store.daily_totals(person["id"], start, end)
        prev_logged = store.daily_totals(person["id"], prev_start, start - timedelta(days=1))
        days = []
        for offset in range(7):
            current = start + timedelta(days=offset)
            totals = logged.get(current.isoformat())
            consumed = {key: totals[key] for key in NUTRIENTS} if totals else dict(_EMPTY)
            days.append(
                {
                    "date": current.isoformat(),
                    "logged": totals is not None,
                    "meals": totals["meals"] if totals else 0,
                    "consumed": consumed,
                    "percent": _percent(consumed, target),
                    "over_kcal": bool(totals) and consumed["kcal"] > target["kcal"],
                }
            )
        logged_days = [day["consumed"] for day in days if day["logged"]]
        week_total = _sum(logged_days) if logged_days else dict(_EMPTY)
        average = _average(logged_days)
        prev_average = _average(list(prev_logged.values()))
        people.append(
            {
                "person": person,
                "target": target,
                "days": days,
                "logged_days": len(logged_days),
                "elapsed_days": elapsed,
                # Finished days with nothing logged; today does not count until it is over.
                "unlogged_past_days": sum(1 for day in days if not day["logged"] and day["date"] < today.isoformat()),
                "total": week_total,
                "target_to_date": {key: round(target[key] * elapsed, 1) for key in NUTRIENTS},
                "remaining_to_date": _diff(week_total, target, elapsed),
                "percent_to_date": _percent(week_total, target, elapsed),
                "daily_average": average,
                "average_percent": _percent(average, target),
                "previous_week": {
                    "logged_days": len(prev_logged),
                    "daily_average": prev_average,
                    "change": {key: round(average[key] - prev_average[key], 1) for key in NUTRIENTS}
                    if prev_logged and logged_days
                    else None,
                },
                "days_over_kcal": sum(1 for day in days if day["over_kcal"]),
                "days_protein_hit": sum(
                    1 for day in days if day["logged"] and day["consumed"]["protein_g"] >= 0.9 * target["protein_g"]
                ),
            }
        )
    return {"start": start.isoformat(), "end": end.isoformat(), "people": people}
