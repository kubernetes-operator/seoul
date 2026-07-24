from __future__ import annotations

from pydantic import BaseModel
from fastapi import APIRouter, Depends, HTTPException

from ..auth import require_session
from ..checks.registry import DOMAINS
from .. import scheduler
from ..db import get_db

router = APIRouter(dependencies=[Depends(require_session)])


class ScheduleUpdate(BaseModel):
    cron: str | None = None  # None/empty disables scheduling
    enabled_domains: list[str] = list(DOMAINS)


@router.get("/schedule")
def get_schedule():
    conn = get_db()
    row = conn.execute("SELECT cron, enabled_domains FROM schedule WHERE id = 1").fetchone()
    if not row:
        return {"cron": None, "enabled_domains": list(DOMAINS)}
    return {"cron": row[0], "enabled_domains": row[1].split(",") if row[1] else list(DOMAINS)}


@router.put("/schedule")
def update_schedule(update: ScheduleUpdate):
    unknown = [d for d in update.enabled_domains if d not in DOMAINS]
    if unknown:
        raise HTTPException(status_code=400, detail=f"unknown domains: {unknown}")

    conn = get_db()
    conn.execute(
        "UPDATE schedule SET cron = ?, enabled_domains = ? WHERE id = 1",
        (update.cron or None, ",".join(update.enabled_domains)),
    )
    conn.commit()
    scheduler.apply_schedule(update.cron or None)
    return get_schedule()
