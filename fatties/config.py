"""Settings from the environment, with a project .env file filling the gaps."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


@dataclass(frozen=True)
class Settings:
    api_key: str | None
    people: list[str]
    analyzer: str
    model: str
    effort: str
    db_path: Path
    timezone: str

    @classmethod
    def load(cls, env_file: Path | None = ROOT / ".env") -> Settings:
        # Real environment variables win over the file, so a deploy can override it.
        env = {**(read_env_file(env_file) if env_file else {}), **os.environ}
        people = [name.strip() for name in env.get("FATTIES_PEOPLE", "Polar,Partner").split(",") if name.strip()]
        db_path = Path(env.get("FATTIES_DB", "data/fatties.db"))
        return cls(
            api_key=env.get("ANTHROPIC_API_KEY") or None,
            people=people or ["Polar", "Partner"],
            analyzer=env.get("FATTIES_ANALYZER", "claude"),
            model=env.get("FATTIES_MODEL", "claude-opus-5-5"),
            effort=env.get("FATTIES_EFFORT", "medium"),
            db_path=db_path if db_path.is_absolute() else ROOT / db_path,
            timezone=env.get("FATTIES_TZ", "Asia/Singapore"),
        )
