from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from ..auth import change_password, require_basic_auth

router = APIRouter(dependencies=[Depends(require_basic_auth)])


class PasswordChange(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8)
    new_username: Optional[str] = None  # omit to keep the current username


@router.put("/auth/password")
def update_password(body: PasswordChange):
    change_password(body.current_password, body.new_password, body.new_username)
    return {"status": "changed"}
