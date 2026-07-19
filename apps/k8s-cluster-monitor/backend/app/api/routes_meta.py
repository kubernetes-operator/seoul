"""Liveness/readiness probes — deliberately NOT behind Basic Auth, since
kubelet's probe requests don't carry credentials. These endpoints reveal
nothing about cluster posture, only this pod's own process health.
"""
from __future__ import annotations

from fastapi import APIRouter

from ..db import get_db

router = APIRouter()


@router.get("/healthz")
def healthz():
    return {"status": "ok"}


@router.get("/readyz")
def readyz():
    try:
        get_db().execute("SELECT 1")
        return {"status": "ready"}
    except Exception as exc:  # noqa: BLE001
        return {"status": "not-ready", "reason": str(exc)}
