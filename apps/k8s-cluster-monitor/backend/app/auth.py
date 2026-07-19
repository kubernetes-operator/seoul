"""HTTP Basic Auth, credentials sourced only from Secret-mounted env vars —
never stored in the app's own database, never hashed-and-stored (there's
nothing to hash against; secrets.compare_digest on the raw value is enough
since the credential's only copy of record is the K8s Secret itself).

If the env vars are absent (e.g. a developer forgot to set them, or local
dev without the Secret mounted), auth fails closed: every request is
rejected rather than silently allowing unauthenticated access. This
deliberately does not fall back to "no auth configured -> allow all",
because that failure mode is exactly how a cluster-posture dashboard would
end up unintentionally open.
"""
from __future__ import annotations

import os
import secrets

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from .config import settings

_security = HTTPBasic()


def require_basic_auth(credentials: HTTPBasicCredentials = Depends(_security)) -> str:
    expected_user = os.environ.get(settings.basic_auth_username_env)
    expected_pass = os.environ.get(settings.basic_auth_password_env)

    if not expected_user or not expected_pass:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Basic auth credentials not configured on the server (missing Secret env vars)",
        )

    user_ok = secrets.compare_digest(credentials.username, expected_user)
    pass_ok = secrets.compare_digest(credentials.password, expected_pass)
    if not (user_ok and pass_ok):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username
