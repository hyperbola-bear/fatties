"""Passwords, login sessions and a brake on password guessing.

Each person has their own password, stored as a salted scrypt hash and set on
the server with ``fatties set-password NAME``. A login gives a signed cookie
that lasts 90 days, so phones rarely ask again. The cookie carries a
fingerprint of the password hash, so changing a password signs out every
device that used the old one.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from collections import defaultdict, deque

MIN_PASSWORD_CHARS = 8
SESSION_SECONDS = 90 * 24 * 3600
COOKIE = "fatties_session"
_SCRYPT = {"n": 2**14, "r": 8, "p": 1}


def hash_password(password: str) -> str:
    if len(password) < MIN_PASSWORD_CHARS:
        raise ValueError(f"password must be at least {MIN_PASSWORD_CHARS} characters")
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, **_SCRYPT)
    return f"scrypt${_SCRYPT['n']}${_SCRYPT['r']}${_SCRYPT['p']}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str | None) -> bool:
    if not stored:
        return False
    try:
        scheme, n, r, p, salt, digest = stored.split("$")
    except ValueError:
        return False
    if scheme != "scrypt":
        return False
    candidate = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=int(n), r=int(r), p=int(p))
    return hmac.compare_digest(candidate.hex(), digest)


def fingerprint(stored: str) -> str:
    return hashlib.sha256(stored.encode()).hexdigest()[:16]


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


class Sessions:
    def __init__(self, secret: str):
        self._key = secret.encode()

    def _sign(self, body: str) -> str:
        return _b64(hmac.new(self._key, body.encode(), hashlib.sha256).digest())

    def issue(self, person_id: int, password_hash: str, now: float | None = None) -> str:
        now = time.time() if now is None else now
        payload = {"pid": person_id, "exp": int(now + SESSION_SECONDS), "pw": fingerprint(password_hash)}
        body = _b64(json.dumps(payload, separators=(",", ":")).encode())
        return f"{body}.{self._sign(body)}"

    def read(self, token: str | None, now: float | None = None) -> dict | None:
        """The payload of a valid, unexpired token, else None. The caller checks the password fingerprint."""
        if not token or "." not in token:
            return None
        body, signature = token.rsplit(".", 1)
        if not hmac.compare_digest(signature, self._sign(body)):
            return None
        try:
            payload = json.loads(_unb64(body))
        except (ValueError, json.JSONDecodeError):
            return None
        if payload.get("exp", 0) < (time.time() if now is None else now):
            return None
        return payload


class LoginLimiter:
    """At most ``limit`` failed logins per name in ``window`` seconds."""

    def __init__(self, limit: int = 10, window: float = 15 * 60):
        self.limit = limit
        self.window = window
        self._failures: dict[str, deque[float]] = defaultdict(deque)

    def _recent(self, name: str, now: float) -> deque[float]:
        failures = self._failures[name]
        while failures and failures[0] < now - self.window:
            failures.popleft()
        return failures

    def blocked(self, name: str, now: float | None = None) -> bool:
        return len(self._recent(name, time.time() if now is None else now)) >= self.limit

    def failed(self, name: str, now: float | None = None) -> None:
        now = time.time() if now is None else now
        self._recent(name, now).append(now)

    def succeeded(self, name: str) -> None:
        self._failures.pop(name, None)
