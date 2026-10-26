"""SQLite storage for people, their targets and their meals. Photos are never stored."""

from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime
from pathlib import Path
from typing import Any

from .analysis import NUTRIENTS, totals
from .targets import DEFAULT_TARGETS, TARGET_KEYS

_SCHEMA = """
CREATE TABLE IF NOT EXISTS people (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    kcal REAL NOT NULL,
    carbs_g REAL NOT NULL,
    protein_g REAL NOT NULL,
    fat_g REAL NOT NULL,
    sugar_g REAL NOT NULL,
    targets_set INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS meals (
    id INTEGER PRIMARY KEY,
    person_id INTEGER NOT NULL REFERENCES people(id),
    eaten_on TEXT NOT NULL,
    eaten_at TEXT NOT NULL,
    meal_type TEXT,
    title TEXT NOT NULL,
    note TEXT,
    items_json TEXT NOT NULL,
    kcal REAL NOT NULL,
    carbs_g REAL NOT NULL,
    protein_g REAL NOT NULL,
    fat_g REAL NOT NULL,
    sugar_g REAL NOT NULL,
    model TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS meals_by_day ON meals(person_id, eaten_on);
"""


class Store:
    def __init__(self, path: Path):
        if str(path) != ":memory:":
            path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(_SCHEMA)

    def ensure_people(self, names: list[str]) -> None:
        with self.db:
            for name in names:
                self.db.execute(
                    "INSERT OR IGNORE INTO people (name, kcal, carbs_g, protein_g, fat_g, sugar_g) VALUES (?, ?, ?, ?, ?, ?)",
                    (name, *(DEFAULT_TARGETS[key] for key in TARGET_KEYS)),
                )

    def people(self) -> list[dict[str, Any]]:
        return [_person(row) for row in self.db.execute("SELECT * FROM people ORDER BY id")]

    def person(self, person_id: int) -> dict[str, Any] | None:
        row = self.db.execute("SELECT * FROM people WHERE id = ?", (person_id,)).fetchone()
        return _person(row) if row else None

    def set_targets(self, person_id: int, targets: dict[str, float]) -> None:
        with self.db:
            self.db.execute(
                "UPDATE people SET kcal = ?, carbs_g = ?, protein_g = ?, fat_g = ?, sugar_g = ?, targets_set = 1 WHERE id = ?",
                (*(float(targets[key]) for key in TARGET_KEYS), person_id),
            )

    def add_meal(
        self,
        *,
        person_id: int,
        eaten_at: datetime,
        meal_type: str | None,
        title: str,
        note: str | None,
        items: list[dict[str, Any]],
        model: str | None,
    ) -> dict[str, Any]:
        meal_totals = totals(items)
        with self.db:
            cursor = self.db.execute(
                "INSERT INTO meals (person_id, eaten_on, eaten_at, meal_type, title, note, items_json,"
                " kcal, carbs_g, protein_g, fat_g, sugar_g, model, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    person_id,
                    eaten_at.date().isoformat(),
                    eaten_at.isoformat(timespec="minutes"),
                    meal_type,
                    title,
                    note,
                    json.dumps(items),
                    *(meal_totals[key] for key in NUTRIENTS),
                    model,
                    datetime.now().astimezone().isoformat(timespec="seconds"),
                ),
            )
        return self.meal(cursor.lastrowid)

    def meal(self, meal_id: int) -> dict[str, Any] | None:
        row = self.db.execute("SELECT * FROM meals WHERE id = ?", (meal_id,)).fetchone()
        return _meal(row) if row else None

    def delete_meal(self, meal_id: int) -> bool:
        with self.db:
            return self.db.execute("DELETE FROM meals WHERE id = ?", (meal_id,)).rowcount > 0

    def meals_on(self, person_id: int, day: date) -> list[dict[str, Any]]:
        rows = self.db.execute(
            "SELECT * FROM meals WHERE person_id = ? AND eaten_on = ? ORDER BY eaten_at", (person_id, day.isoformat())
        )
        return [_meal(row) for row in rows]

    def daily_totals(self, person_id: int, start: date, end: date) -> dict[str, dict[str, float]]:
        """Summed nutrients per day with at least one meal, from start to end inclusive."""
        rows = self.db.execute(
            "SELECT eaten_on, COUNT(*) AS meals, SUM(kcal) AS kcal, SUM(carbs_g) AS carbs_g, SUM(protein_g) AS protein_g,"
            " SUM(fat_g) AS fat_g, SUM(sugar_g) AS sugar_g FROM meals"
            " WHERE person_id = ? AND eaten_on BETWEEN ? AND ? GROUP BY eaten_on",
            (person_id, start.isoformat(), end.isoformat()),
        )
        return {
            row["eaten_on"]: {"meals": row["meals"], **{key: round(row[key], 1) for key in NUTRIENTS}} for row in rows
        }


def _person(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "name": row["name"],
        "targets": {key: row[key] for key in TARGET_KEYS},
        "targets_set": bool(row["targets_set"]),
    }


def _meal(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "person_id": row["person_id"],
        "eaten_on": row["eaten_on"],
        "eaten_at": row["eaten_at"],
        "meal_type": row["meal_type"],
        "title": row["title"],
        "note": row["note"],
        "items": json.loads(row["items_json"]),
        "totals": {key: row[key] for key in NUTRIENTS},
        "model": row["model"],
    }
