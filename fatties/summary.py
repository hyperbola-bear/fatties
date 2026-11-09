"""Calendar day/week/month/year comparisons against historical plans.

Each day is compared with the plan in force that day. Days before a person's
first plan have no target and are left out of every comparison.

Weeks run Monday to Sunday. Averages are over days with at least one logged
meal, so a day nobody logged does not look like a perfect zero-calorie day.
"""

from __future__ import annotations

import calendar
from datetime import date, timedelta
from typing import Any

from .analysis import NUTRIENTS
from .store import Store

_EMPTY = {key: 0.0 for key in NUTRIENTS}


def plan_on(plans: list[dict[str, Any]], day: date) -> dict[str, Any] | None:
    """The plan in force on day: the latest one that started on or before it."""
    current = None
    for plan in plans:
        if plan["starts_on"] <= day.isoformat():
            current = plan
    return current


def plan_progress(plan: dict[str, Any] | None, day: date) -> dict[str, Any] | None:
    if not plan:
        return None
    details = plan["details"]
    return {
        "starts_on": plan["starts_on"],
        "day_number": (day - date.fromisoformat(plan["starts_on"])).days + 1,
        "weight_kg": details.get("weight_kg"),
        "target_weight_kg": details.get("target_weight_kg"),
        "pace_kg_per_week": details.get("pace_kg_per_week"),
        "finish_on": details.get("finish_on"),
    }


def _diff(consumed: dict[str, float], target: dict[str, float]) -> dict[str, float]:
    return {key: round(target[key] - consumed[key], 1) for key in NUTRIENTS}


def _percent(consumed: dict[str, float], target: dict[str, float]) -> dict[str, int]:
    return {key: round(100 * consumed[key] / target[key]) if target[key] else 0 for key in NUTRIENTS}


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
        plan = plan_on(store.plans(person["id"]), day)
        target = plan["targets"] if plan else None
        people.append(
            {
                "person": person,
                "plan": plan_progress(plan, day),
                "consumed": consumed,
                "target": target,
                "remaining": _diff(consumed, target) if target else None,
                "percent": _percent(consumed, target) if target else None,
                "meals": meals,
            }
        )
    return {"date": day.isoformat(), "people": people}


def week_start(day: date) -> date:
    return day - timedelta(days=day.weekday())


def week_summary(store: Store, day: date, today: date) -> dict[str, Any]:
    return period_summary(store, day, today, "week")


def period_bounds(day: date, period: str) -> tuple[date, date]:
    if period == "week":
        start = week_start(day)
        return start, start + timedelta(days=6)
    if period == "month":
        return day.replace(day=1), day.replace(day=calendar.monthrange(day.year, day.month)[1])
    if period == "year":
        return date(day.year, 1, 1), date(day.year, 12, 31)
    raise ValueError("period must be week, month, or year")


def period_summary(store: Store, day: date, today: date, period: str) -> dict[str, Any]:
    start, end = period_bounds(day, period)
    prev_start, prev_end = period_bounds(start - timedelta(days=1), period)
    people = []
    for person in store.people():
        plans = store.plans(person["id"])
        logged = store.daily_totals(person["id"], start, end)
        prev_logged = store.daily_totals(person["id"], prev_start, min(prev_end, today))
        days = []
        for offset in range((end - start).days + 1):
            current = start + timedelta(days=offset)
            totals = logged.get(current.isoformat())
            consumed = {key: totals[key] for key in NUTRIENTS} if totals else dict(_EMPTY)
            plan = plan_on(plans, current)
            target = plan["targets"] if plan else None
            days.append(
                {
                    "date": current.isoformat(),
                    "logged": totals is not None,
                    "meals": totals["meals"] if totals else 0,
                    "consumed": consumed,
                    "target": target,
                    "percent": _percent(consumed, target) if target else None,
                    "over_kcal": bool(totals and target) and consumed["kcal"] > target["kcal"],
                    "protein_hit": bool(totals and target) and consumed["protein_g"] >= 0.9 * target["protein_g"],
                }
            )
        # Only days under a plan count towards the comparison.
        planned = [d for d in days if d["target"]]
        begun = [d for d in planned if d["date"] <= today.isoformat()]
        logged_planned = [d for d in begun if d["logged"]]
        week_total = _sum([d["consumed"] for d in begun if d["logged"]]) if begun else dict(_EMPTY)
        target_to_date = _sum([d["target"] for d in begun]) if begun else dict(_EMPTY)
        average = _average([d["consumed"] for d in logged_planned])
        average_target = _average([d["target"] for d in logged_planned])
        all_logged = [d["consumed"] for d in days if d["logged"] and d["date"] <= today.isoformat()]
        week_average = _average(all_logged)
        prev_average = _average(list(prev_logged.values()))
        last_day = min(end, today)
        previous = {
            "start": prev_start.isoformat(), "end": prev_end.isoformat(),
            "logged_days": len(prev_logged), "daily_average": prev_average,
            "change": {key: round(week_average[key] - prev_average[key], 1) for key in NUTRIENTS}
            if prev_logged and all_logged else None,
        }
        buckets = []
        for month in range(1, 13) if period == "year" else []:
            group = [d for d in days if date.fromisoformat(d["date"]).month == month and d["date"] <= today.isoformat()]
            recorded = [d for d in group if d["logged"]]
            targeted = [d for d in recorded if d["target"]]
            buckets.append({
                "label": calendar.month_abbr[month], "date": date(start.year, month, 1).isoformat(),
                "logged": bool(recorded), "logged_days": len(recorded),
                "consumed": _average([d["consumed"] for d in recorded]),
                "target": _average([d["target"] for d in targeted]) if targeted else None,
                "total": _sum([d["consumed"] for d in recorded]),
            })
        people.append(
            {
                "person": person,
                "plan": plan_progress(plan_on(plans, last_day), last_day),
                "target": plan_on(plans, last_day)["targets"] if plan_on(plans, last_day) else None,
                "days": days,
                "buckets": buckets,
                "all_logged_days": len(all_logged),
                "elapsed_days": max(0, (min(end, today) - start).days + 1),
                "all_daily_average": week_average,
                "all_total": _sum(all_logged),
                "logged_days": len(logged_planned),
                "planned_days_begun": len(begun),
                # Finished planned days with nothing logged; today does not count until it is over.
                "unlogged_past_days": sum(1 for d in begun if not d["logged"] and d["date"] < today.isoformat()),
                "total": week_total,
                "target_to_date": target_to_date,
                "remaining_to_date": _diff(week_total, target_to_date),
                "daily_average": average,
                "average_target": average_target,
                "average_percent": _percent(average, average_target) if logged_planned else None,
                "previous_week": previous,
                "previous_period": previous,
                "days_over_kcal": sum(1 for d in begun if d["over_kcal"]),
                "days_protein_hit": sum(1 for d in begun if d["protein_hit"]),
            }
        )
    return {"period": period, "start": start.isoformat(), "end": end.isoformat(), "people": people}
