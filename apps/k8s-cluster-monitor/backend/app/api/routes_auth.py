from __future__ import annotations

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field

from .. import auth, login_guard
from ..auth import require_session
from ..db import get_db

router = APIRouter()


class LoginBody(BaseModel):
    username: str
    password: str


class NewUserBody(BaseModel):
    username: str
    password: str = Field(min_length=8)
    role: str = "viewer"
    pages: list[str] | None = None


class PasswordChange(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8)


def _user_summary(username: str) -> dict:
    role,pages=auth.user_access(username)
    return {'username':username,'role':role,'pages':auth.page_policy.effective('seoul',role,pages)}


def _is_https(request: Request) -> bool:
    proto = request.headers.get("x-forwarded-proto", "").split(",")[0].strip().lower()
    return request.url.scheme == "https" or proto == "https"


def _set_session_cookie(request: Request, response: Response, token: str) -> None:
    # Secure whenever the browser reached us over HTTPS (Gateway/Cloudflare set
    # X-Forwarded-Proto), so the session never travels in clear text there;
    # left off only for plain-HTTP `kubectl port-forward` debugging.
    response.set_cookie(
        key=auth.SESSION_COOKIE, value=token, httponly=True, secure=_is_https(request),
        samesite="lax", path="/", max_age=auth.SESSION_TTL_SECONDS,
    )


@router.post("/login")
def login(body: LoginBody, request: Request, response: Response):
    wait = login_guard.retry_after(body.username)
    if wait:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                            detail=f"로그인 실패가 반복되어 잠시 잠겼습니다. {wait // 60 + 1}분 후 다시 시도하세요",
                            headers={"Retry-After": str(wait)})
    if not auth.verify_user(get_db(), body.username, body.password):
        login_guard.record_failure(body.username)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="사용자명 또는 비밀번호가 올바르지 않습니다")
    login_guard.record_success(body.username)
    token = auth.create_session(body.username)
    _set_session_cookie(request, response, token)
    return _user_summary(body.username)


@router.post("/logout")
def logout(response: Response, kct_session: str = Cookie(default=None), _: str = Depends(require_session)):
    auth.delete_session(kct_session)
    response.delete_cookie(key=auth.SESSION_COOKIE, path="/")
    return {"status": "logged_out"}


@router.get("/session")
def whoami(current: str = Depends(require_session)):
    return _user_summary(current)


@router.get("/users")
def get_users(_: str = Depends(auth.require_admin)):
    return {"users": auth.list_users()}


@router.post("/users", status_code=status.HTTP_201_CREATED)
def add_user(body: NewUserBody, _: str = Depends(auth.require_admin)):
    auth.create_user(body.username, body.password, body.role, body.pages)
    return {"status": "created", "username": body.username.strip()}


@router.put("/auth/password")
def update_password(body: PasswordChange, current: str = Depends(require_session),
                    kct_session: str = Cookie(default=None)):
    auth.change_user_password(current, body.current_password, body.new_password, keep_session=kct_session)
    return {"status": "changed"}


@router.get('/ui/session')
def ui_session(current: str = Depends(require_session)):
    role,pages=auth.user_access(current)
    return {'username':current,'role':role,'pages':auth.page_policy.effective('seoul',role,pages),
            'availablePages':auth.page_policy.PAGES['seoul'],'canManageUsers':role=='admin','passwordMinLength':8}


class ConfirmedPassword(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8)
    confirm_password: str = Field(min_length=8)


@router.post('/ui/password')
def ui_password(body: ConfirmedPassword, current: str = Depends(require_session),
                kct_session: str = Cookie(default=None)):
    if body.new_password!=body.confirm_password:raise HTTPException(400,'새 비밀번호가 일치하지 않습니다')
    auth.change_user_password(current,body.current_password,body.new_password,keep_session=kct_session)
    return {'ok':True}


@router.get('/ui/users')
def ui_users(_: str=Depends(auth.require_admin)):
    return auth.list_user_details()


@router.post('/ui/users',status_code=201)
def ui_add_user(body: NewUserBody,_: str=Depends(auth.require_admin)):
    return add_user(body,_)


class UserAccessPatch(BaseModel):
    role: str
    pages: list[str] | None = None
    password: str | None = None


@router.patch('/ui/users/{username}')
def ui_update_user(username: str,body: UserAccessPatch,_: str=Depends(auth.require_admin)):
    auth.update_user_access(username,body.role,body.pages,body.password)
    return {'ok':True}
