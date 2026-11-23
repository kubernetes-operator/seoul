"""Login brute-force throttle (in-memory; this app runs as a single replica).

Keyed by username (normalized) so guessing one account's password is capped
no matter how many source IPs an attacker rotates through. After
MAX_FAILURES failed attempts inside WINDOW_SECONDS the account is locked for
LOCK_SECONDS; a successful login clears the counter. Locking is per account
and time-boxed, so the worst an attacker can do is delay a real login by
LOCK_SECONDS — far better than an unbounded online guessing window on a
publicly reachable dashboard whose default credential is documented.
"""
from __future__ import annotations

import threading
import time

MAX_FAILURES = 5
WINDOW_SECONDS = 15 * 60
LOCK_SECONDS = 15 * 60

_lock = threading.Lock()
_failures: dict[str, list[float]] = {}
_locked_until: dict[str, float] = {}


def _key(username: str) -> str:
    return (username or "").strip().lower()[:128]


def retry_after(username: str, now: float | None = None) -> int:
    """Seconds until this account may try again (0 = allowed)."""
    now = now or time.time()
    with _lock:
        until = _locked_until.get(_key(username), 0)
        return max(0, int(until - now + 0.999))


def record_failure(username: str, now: float | None = None) -> None:
    now = now or time.time()
    key = _key(username)
    with _lock:
        recent = [t for t in _failures.get(key, []) if now - t < WINDOW_SECONDS]
        recent.append(now)
        if len(recent) >= MAX_FAILURES:
            _locked_until[key] = now + LOCK_SECONDS
            recent = []
        _failures[key] = recent
        if len(_failures) > 10_000:  # bound memory against username spraying
            _failures.clear()


def record_success(username: str) -> None:
    key = _key(username)
    with _lock:
        _failures.pop(key, None)
        _locked_until.pop(key, None)


def reset() -> None:
    with _lock:
        _failures.clear()
        _locked_until.clear()
