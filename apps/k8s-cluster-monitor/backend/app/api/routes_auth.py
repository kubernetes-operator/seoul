from __future__ import annotations

from fastapi import APIRouter, Cookie, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field

from .. import auth
from ..auth import require_session
from ..db import get_db

router = APIRouter()


class LoginBody(BaseModel):
    username: str
    password: str


class NewUserBody(BaseModel):
    username: str
    password: str = Field(min_length=8)


class PasswordChange(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8)


def _set_session_cookie(response: Response, token: str) -> None:
    # Secure is intentionally left off so `kubectl port-forward` over plain
    # HTTP works too; in normal use the app is only reached over HTTPS via the
    # Gateway/Cloudflare. httponly + SameSite=Lax mitigate XSS/CSRF exposure.
    response.set_cookie(
        key=auth.SESSION_COOKIE, value=token, httponly=True,
        samesite="lax", path="/", max_age=auth.SESSION_TTL_SECONDS,
    )


@router.post("/login")
def login(body: LoginBody, response: Response):
    if not auth.verify_user(get_db(), body.username, body.password):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="사용자명 또는 비밀번호가 올바르지 않습니다")
    token = auth.create_session(body.username)
    _set_session_cookie(response, token)
    return {"username": body.username}


@router.post("/logout")
def logout(response: Response, kct_session: str = Cookie(default=None), _: str = Depends(require_session)):
    auth.delete_session(kct_session)
    response.delete_cookie(key=auth.SESSION_COOKIE, path="/")
    return {"status": "logged_out"}


@router.get("/session")
def whoami(current: str = Depends(require_session)):
    return {"username": current}


@router.get("/users")
def get_users(_: str = Depends(require_session)):
    return {"users": auth.list_users()}


@router.post("/users", status_code=status.HTTP_201_CREATED)
def add_user(body: NewUserBody, _: str = Depends(require_session)):
    auth.create_user(body.username, body.password)
    return {"status": "created", "username": body.username.strip()}


@router.put("/auth/password")
def update_password(body: PasswordChange, current: str = Depends(require_session)):
    auth.change_user_password(current, body.current_password, body.new_password)
    return {"status": "changed"}
