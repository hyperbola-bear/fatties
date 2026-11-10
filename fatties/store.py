"""SQLite storage for people, plans, meals, private photo references and widget access.

A plan holds daily targets from the day it is saved. Saving a new plan later
starts a new one from that day and keeps the old one for the days before, so
past days are always compared with the targets that applied at the time.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
import threading
import time
from datetime import date, datetime
from functools import wraps
from pathlib import Path
from typing import Any

from .analysis import NUTRIENTS, totals
from .targets import TARGET_KEYS

_SCHEMA = """
CREATE TABLE IF NOT EXISTS people (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    password_hash TEXT
);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS plans (
    id INTEGER PRIMARY KEY,
    person_id INTEGER NOT NULL REFERENCES people(id),
    starts_on TEXT NOT NULL,
    kcal REAL NOT NULL,
    carbs_g REAL NOT NULL,
    protein_g REAL NOT NULL,
    fat_g REAL NOT NULL,
    sugar_g REAL NOT NULL,
    details_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (person_id, starts_on)
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
CREATE TABLE IF NOT EXISTS widget_credentials (
    token_hash TEXT PRIMARY KEY,
    person_id INTEGER NOT NULL REFERENCES people(id),
    password_fingerprint TEXT NOT NULL,
    expires_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS photo_cleanup (photo_key TEXT PRIMARY KEY);
"""


def locked(method):
    """The shared SQLite connection must serialize reads as well as writes."""
    @wraps(method)
    def call(self, *args, **kwargs):
        with self.lock:
            return method(self, *args, **kwargs)
    return call


class Store:
    def __init__(self, path: Path):
        self.lock = threading.RLock()
        if str(path) != ":memory:":
            path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(_SCHEMA)
        columns = {row["name"] for row in self.db.execute("PRAGMA table_info(people)")}
        if "password_hash" not in columns:  # databases made before login existed
            self.db.execute("ALTER TABLE people ADD COLUMN password_hash TEXT")
        meal_columns = {row["name"] for row in self.db.execute("PRAGMA table_info(meals)")}
        for column in ["photo_key", "request_id"]:
            if column not in meal_columns:
                self.db.execute(f"ALTER TABLE meals ADD COLUMN {column} TEXT")
        self.db.execute("CREATE UNIQUE INDEX IF NOT EXISTS meal_request ON meals(person_id, request_id)")
        self.db.commit()

    def ensure_people(self, names: list[str]) -> None:
        with self.lock, self.db:
            for name in names:
                self.db.execute("INSERT OR IGNORE INTO people (name) VALUES (?)", (name,))

    @locked
    def people(self) -> list[dict[str, Any]]:
        return [self._person(row) for row in self.db.execute("SELECT * FROM people ORDER BY id")]

    @locked
    def person(self, person_id: int) -> dict[str, Any] | None:
        row = self.db.execute("SELECT * FROM people WHERE id = ?", (person_id,)).fetchone()
        return self._person(row) if row else None

    @locked
    def _person(self, row: sqlite3.Row) -> dict[str, Any]:
        plans = self.plans(row["id"])
        return {"id": row["id"], "name": row["name"], "plan": plans[-1] if plans else None}

    @locked
    def person_by_name(self, name: str) -> dict[str, Any] | None:
        row = self.db.execute("SELECT * FROM people WHERE name = ? COLLATE NOCASE", (name.strip(),)).fetchone()
        return self._person(row) if row else None

    @locked
    def password_hash(self, person_id: int) -> str | None:
        row = self.db.execute("SELECT password_hash FROM people WHERE id = ?", (person_id,)).fetchone()
        return row["password_hash"] if row else None

    def set_password_hash(self, person_id: int, password_hash: str) -> None:
        with self.lock, self.db:
            self.db.execute("UPDATE people SET password_hash = ? WHERE id = ?", (password_hash, person_id))
            self.db.execute("DELETE FROM widget_credentials WHERE person_id = ?", (person_id,))

    @locked
    def anyone_has_password(self) -> bool:
        return self.db.execute("SELECT 1 FROM people WHERE password_hash IS NOT NULL LIMIT 1").fetchone() is not None

    def session_secret(self) -> str:
        """A random key for signing login cookies, made once and kept in the database."""
        with self.lock, self.db:
            self.db.execute(
                "INSERT OR IGNORE INTO settings (key, value) VALUES ('session_secret', ?)", (secrets.token_hex(32),)
            )
        return self.db.execute("SELECT value FROM settings WHERE key = 'session_secret'").fetchone()["value"]

    def save_plan(self, person_id: int, starts_on: date, targets: dict[str, float], details: dict[str, Any]) -> None:
        """Start a plan on starts_on. A second save on the same day replaces the first."""
        with self.lock, self.db:
            self.db.execute(
                "INSERT OR REPLACE INTO plans (person_id, starts_on, kcal, carbs_g, protein_g, fat_g, sugar_g,"
                " details_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    person_id,
                    starts_on.isoformat(),
                    *(float(targets[key]) for key in TARGET_KEYS),
                    json.dumps(details),
                    datetime.now().astimezone().isoformat(timespec="seconds"),
                ),
            )

    @locked
    def plans(self, person_id: int) -> list[dict[str, Any]]:
        """Every plan for a person, oldest first."""
        rows = self.db.execute("SELECT * FROM plans WHERE person_id = ? ORDER BY starts_on", (person_id,))
        return [
            {
                "starts_on": row["starts_on"],
                "targets": {key: row[key] for key in TARGET_KEYS},
                "details": json.loads(row["details_json"]),
            }
            for row in rows
        ]

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
        photo_key: str | None = None,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        meal_totals = totals(items)
        with self.lock, self.db:
            cursor = self.db.execute(
                "INSERT INTO meals (person_id, eaten_on, eaten_at, meal_type, title, note, items_json,"
                " kcal, carbs_g, protein_g, fat_g, sugar_g, model, created_at, photo_key, request_id)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
                    datetime.now().astimezone().isoformat(timespec="microseconds"),
                    photo_key,
                    request_id,
                ),
            )
            if photo_key:
                self.db.execute("DELETE FROM photo_cleanup WHERE photo_key = ?", (photo_key,))
        return self.meal(cursor.lastrowid)

    @locked
    def meal(self, meal_id: int) -> dict[str, Any] | None:
        row = self.db.execute("SELECT * FROM meals WHERE id = ?", (meal_id,)).fetchone()
        return _meal(row) if row else None

    def delete_meal(self, meal_id: int) -> bool:
        with self.lock, self.db:
            key = self.photo_key(meal_id)
            if key:
                self.db.execute("INSERT OR IGNORE INTO photo_cleanup VALUES (?)", (key,))
            return self.db.execute("DELETE FROM meals WHERE id = ?", (meal_id,)).rowcount > 0

    @locked
    def photo_key(self, meal_id: int) -> str | None:
        row = self.db.execute("SELECT photo_key FROM meals WHERE id = ?", (meal_id,)).fetchone()
        return row["photo_key"] if row else None

    @locked
    def photo_keys(self) -> list[str]:
        return [row[0] for row in self.db.execute(
            "SELECT DISTINCT photo_key FROM meals WHERE photo_key IS NOT NULL ORDER BY photo_key"
        )]

    @locked
    def request_meal(self, person_id: int, request_id: str) -> dict | None:
        row = self.db.execute("SELECT * FROM meals WHERE person_id = ? AND request_id = ?", (person_id, request_id)).fetchone()
        return _meal(row) if row else None

    @locked
    def latest_meal(self, person_id: int) -> dict | None:
        row = self.db.execute(
            "SELECT * FROM meals WHERE person_id = ? ORDER BY julianday(created_at) DESC, id DESC LIMIT 1", (person_id,)
        ).fetchone()
        return _meal(row) if row else None

    def schedule_photo_cleanup(self, key: str) -> None:
        with self.lock, self.db:
            self.db.execute("INSERT OR IGNORE INTO photo_cleanup VALUES (?)", (key,))

    @locked
    def photo_cleanup_keys(self) -> list[str]:
        return [row[0] for row in self.db.execute("SELECT photo_key FROM photo_cleanup LIMIT 10")]

    def finish_photo_cleanup(self, key: str) -> None:
        with self.lock, self.db:
            self.db.execute("DELETE FROM photo_cleanup WHERE photo_key = ?", (key,))

    def issue_widget_credential(self, person_id: int) -> tuple[str, float]:
        from .auth import SESSION_SECONDS, fingerprint

        token = secrets.token_urlsafe(32)
        expires_at = time.time() + SESSION_SECONDS
        with self.lock, self.db:
            self.db.execute("DELETE FROM widget_credentials WHERE expires_at <= ?", (time.time(),))
            self.db.execute(
                "INSERT INTO widget_credentials VALUES (?, ?, ?, ?)",
                (hashlib.sha256(token.encode()).hexdigest(), person_id, fingerprint(self.password_hash(person_id)), expires_at),
            )
        return token, expires_at

    @locked
    def widget_person(self, token: str) -> dict | None:
        from .auth import fingerprint

        if not token or len(token) > 200:
            return None
        row = self.db.execute("SELECT * FROM widget_credentials WHERE token_hash = ?", (hashlib.sha256(token.encode()).hexdigest(),)).fetchone()
        if row is None or row["expires_at"] <= time.time():
            return None
        password = self.password_hash(row["person_id"])
        if not password or fingerprint(password) != row["password_fingerprint"]:
            return None
        return self.person(row["person_id"])

    def revoke_widget_credentials(self, person_id: int) -> None:
        with self.lock, self.db:
            self.db.execute("DELETE FROM widget_credentials WHERE person_id = ?", (person_id,))

    @locked
    def widget_connections(self, person_id: int) -> list[dict]:
        return [{"expires_at": row[0]} for row in self.db.execute(
            "SELECT expires_at FROM widget_credentials WHERE person_id = ? AND expires_at > ? ORDER BY expires_at",
            (person_id, time.time()),
        )]

    @locked
    def meals_on(self, person_id: int, day: date) -> list[dict[str, Any]]:
        rows = self.db.execute(
            "SELECT * FROM meals WHERE person_id = ? AND eaten_on = ? ORDER BY eaten_at", (person_id, day.isoformat())
        )
        return [_meal(row) for row in rows]

    @locked
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
        "created_at": row["created_at"],
        "has_photo": bool(row["photo_key"]),
    }
