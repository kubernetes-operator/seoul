"""HTTP Basic Auth. Credentials are stored in SQLite (PBKDF2-HMAC hash + a
random salt, never plaintext), not read from the K8s Secret on every
request — that's what makes an in-app "change password" feature possible
without this app ever needing write access to Secret objects. This app's
ClusterRole grants zero verbs on `secrets` (see checks/security.py and
deploy/README.md); a self-service password change must not become the one
exception.

The K8s Secret's BASIC_AUTH_USERNAME/PASSWORD env vars are used exactly
once, to seed the credential the first time this pod ever starts against an
empty database. After that the database is authoritative, and changing the
Secret has no effect until the database's `auth_credential` row is cleared.

Recovery if locked out (no Secret access needed, just cluster-admin exec):
    kubectl -n k8s-cluster-monitor exec deploy/k8s-cluster-monitor -- \\
      python3 -c "import sqlite3; sqlite3.connect('/data/monitor.db').execute('DELETE FROM auth_credential').close()"
    kubectl -n k8s-cluster-monitor rollout restart deployment/k8s-cluster-monitor
This re-seeds from whatever the Secret currently holds on next boot.
"""
from __future__ import annotations

import hashlib
import os
import secrets
import sqlite3
import time
from typing import Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from .config import settings
from .db import get_db

_security = HTTPBasic()
_PBKDF2_ITERATIONS = 390_000


def _hash_password(password: str, salt: bytes) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _PBKDF2_ITERATIONS).hex()


def _seed_from_env_if_absent(conn: sqlite3.Connection) -> None:
    if conn.execute("SELECT id FROM auth_credential WHERE id = 1").fetchone():
        return
    env_user = os.environ.get(settings.basic_auth_username_env)
    env_pass = os.environ.get(settings.basic_auth_password_env)
    if not env_user or not env_pass:
        return  # nothing to seed with — require_basic_auth fails closed below
    salt = secrets.token_bytes(16)
    conn.execute(
        "INSERT INTO auth_credential (id, username, password_hash, salt, updated_at) VALUES (1, ?, ?, ?, ?)",
        (env_user, _hash_password(env_pass, salt), salt.hex(), time.time()),
    )
    conn.commit()


def _get_credential(conn: sqlite3.Connection) -> Optional[tuple]:
    _seed_from_env_if_absent(conn)
    return conn.execute(
        "SELECT username, password_hash, salt FROM auth_credential WHERE id = 1"
    ).fetchone()


def require_basic_auth(credentials: HTTPBasicCredentials = Depends(_security)) -> str:
    row = _get_credential(get_db())
    if not row:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="No auth credential configured (missing Secret env vars on first boot)",
        )
    stored_user, stored_hash, stored_salt_hex = row
    computed_hash = _hash_password(credentials.password, bytes.fromhex(stored_salt_hex))

    user_ok = secrets.compare_digest(credentials.username, stored_user)
    pass_ok = secrets.compare_digest(computed_hash, stored_hash)
    if not (user_ok and pass_ok):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username


def change_password(current_password: str, new_password: str, new_username: Optional[str] = None) -> None:
    """Verifies current_password against the stored hash, then replaces it.
    Raises HTTPException(401) if current_password is wrong, (503) if no
    credential is configured yet at all.
    """
    conn = get_db()
    row = _get_credential(conn)
    if not row:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="No credential configured")

    stored_user, stored_hash, stored_salt_hex = row
    if not secrets.compare_digest(_hash_password(current_password, bytes.fromhex(stored_salt_hex)), stored_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Current password is incorrect")

    new_salt = secrets.token_bytes(16)
    conn.execute(
        "UPDATE auth_credential SET username = ?, password_hash = ?, salt = ?, updated_at = ? WHERE id = 1",
        (new_username or stored_user, _hash_password(new_password, new_salt), new_salt.hex(), time.time()),
    )
    conn.commit()
