"""Single shared SQLite connection for the process (single-replica app,
one writer at a time — the scheduler and on-demand run endpoint never run
concurrently because both go through registry.run_domain synchronously).
"""
from __future__ import annotations

import sqlite3

from .config import settings
from .models import get_connection

_connection: sqlite3.Connection | None = None


def get_db() -> sqlite3.Connection:
    global _connection
    if _connection is None:
        _connection = get_connection(settings.db_path)
        # Seed default users (admin/password) on first open, so login works
        # even outside the app lifespan (e.g. TestClient without a context
        # manager). Idempotent. Imported lazily to avoid an auth<->db cycle.
        from .auth import ensure_seed
        ensure_seed(_connection)
    return _connection
