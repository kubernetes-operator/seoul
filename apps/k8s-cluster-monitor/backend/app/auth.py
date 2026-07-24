"""Form-based login with server-side sessions + multi-user credentials.

Users live in SQLite (`users` table: PBKDF2-HMAC hash + a per-user random
salt, never plaintext). Sessions are opaque server-side tokens (`sessions`
table) referenced by an httponly cookie — no signing key to manage, sessions
are individually revocable (logout), and they survive pod restarts (SQLite on
the PVC).

This app's ClusterRole grants zero verbs on `secrets` (see checks/security.py
and deploy/README.md); storing credentials in SQLite rather than K8s Secrets
is what keeps a self-service login / add-user / change-password feature
possible without that write access ever becoming the one exception.

Seeding (ensure_seed, called once at startup): the first time this runs
against a users-less database it seeds the default **admin / password**
(intentionally weak per request — change it immediately from the Settings tab;
the app's own security audit flags weak default credentials), and migrates any
pre-existing single Basic-Auth credential from the legacy `auth_credential`
table so prior access isn't lost on upgrade.
"""
from __future__ import annotations

import hashlib
import secrets
import sqlite3
import time
from typing import Optional

from fastapi import Cookie, HTTPException, status

from .db import get_db

_PBKDF2_ITERATIONS = 390_000

SESSION_COOKIE = "kct_session"
SESSION_TTL_SECONDS = 7 * 24 * 3600  # 7 days
MIN_PASSWORD_LEN = 8

DEFAULT_USERNAME = "admin"
DEFAULT_PASSWORD = "password"  # weak-by-request seed; overridable from the UI


def _hash_password(password: str, salt: bytes) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _PBKDF2_ITERATIONS).hex()


def _user_exists(conn: sqlite3.Connection, username: str) -> bool:
    return conn.execute("SELECT 1 FROM users WHERE username = ?", (username,)).fetchone() is not None


def _insert_user(conn: sqlite3.Connection, username: str, password: str) -> None:
    salt = secrets.token_bytes(16)
    conn.execute(
        "INSERT INTO users (username, password_hash, salt, created_at) VALUES (?, ?, ?, ?)",
        (username, _hash_password(password, salt), salt.hex(), time.time()),
    )


def ensure_seed(conn: sqlite3.Connection) -> None:
    """Idempotent: seeds default users only when the `users` table is empty."""
    if conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] > 0:
        return
    # Preserve a legacy single Basic-Auth credential (unless its name is the
    # default 'admin', which the requested seed below covers anyway).
    legacy = conn.execute(
        "SELECT username, password_hash, salt FROM auth_credential WHERE id = 1"
    ).fetchone()
    if legacy and legacy[0] != DEFAULT_USERNAME:
        conn.execute(
            "INSERT INTO users (username, password_hash, salt, created_at) VALUES (?, ?, ?, ?)",
            (legacy[0], legacy[1], legacy[2], time.time()),
        )
    if not _user_exists(conn, DEFAULT_USERNAME):
        _insert_user(conn, DEFAULT_USERNAME, DEFAULT_PASSWORD)
    conn.commit()


# --------------------------------------------------------------------------
# Users
# --------------------------------------------------------------------------
def verify_user(conn: sqlite3.Connection, username: str, password: str) -> bool:
    row = conn.execute(
        "SELECT password_hash, salt FROM users WHERE username = ?", (username,)
    ).fetchone()
    if not row:
        # Spend a comparable amount of time hashing even for unknown users to
        # blunt username-enumeration timing side channels.
        _hash_password(password, b"0" * 16)
        return False
    stored_hash, salt_hex = row
    return secrets.compare_digest(_hash_password(password, bytes.fromhex(salt_hex)), stored_hash)


def create_user(username: str, password: str) -> None:
    username = username.strip()
    if not username:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="사용자명을 입력하세요")
    if len(password) < MIN_PASSWORD_LEN:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"비밀번호는 {MIN_PASSWORD_LEN}자 이상이어야 합니다",
        )
    conn = get_db()
    if _user_exists(conn, username):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="이미 존재하는 사용자명입니다")
    _insert_user(conn, username, password)
    conn.commit()


def list_users() -> list[str]:
    conn = get_db()
    return [r[0] for r in conn.execute("SELECT username FROM users ORDER BY username").fetchall()]


def change_user_password(username: str, current_password: str, new_password: str) -> None:
    conn = get_db()
    if not verify_user(conn, username, current_password):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="현재 비밀번호가 올바르지 않습니다")
    if len(new_password) < MIN_PASSWORD_LEN:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"비밀번호는 {MIN_PASSWORD_LEN}자 이상이어야 합니다",
        )
    salt = secrets.token_bytes(16)
    conn.execute(
        "UPDATE users SET password_hash = ?, salt = ? WHERE username = ?",
        (_hash_password(new_password, salt), salt.hex(), username),
    )
    conn.commit()


# --------------------------------------------------------------------------
# Sessions
# --------------------------------------------------------------------------
def create_session(username: str) -> str:
    conn = get_db()
    token = secrets.token_urlsafe(32)
    now = time.time()
    conn.execute(
        "INSERT INTO sessions (token, username, created_at, expires_at) VALUES (?, ?, ?, ?)",
        (token, username, now, now + SESSION_TTL_SECONDS),
    )
    conn.commit()
    return token


def delete_session(token: Optional[str]) -> None:
    if not token:
        return
    conn = get_db()
    conn.execute("DELETE FROM sessions WHERE token = ?", (token,))
    conn.commit()


def _session_user(token: Optional[str]) -> Optional[str]:
    if not token:
        return None
    conn = get_db()
    row = conn.execute("SELECT username, expires_at FROM sessions WHERE token = ?", (token,)).fetchone()
    if not row:
        return None
    username, expires_at = row
    if expires_at < time.time():
        conn.execute("DELETE FROM sessions WHERE token = ?", (token,))
        conn.commit()
        return None
    return username


def require_session(kct_session: Optional[str] = Cookie(default=None)) -> str:
    """FastAPI dependency: resolves the session cookie to a username or 401s."""
    user = _session_user(kct_session)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="로그인이 필요합니다")
    return user
