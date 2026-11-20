"""Domains & pages tab data: per-URL probe rows from the latest "routes" run.
Page access ('routes') is enforced by auth.check_page_access via require_session."""
from __future__ import annotations

from fastapi import APIRouter, Depends

from ..auth import require_session
from ..db import get_db
from ..models import latest_route_probes

router = APIRouter(dependencies=[Depends(require_session)])


@router.get("/routes/latest")
def routes_latest():
    return latest_route_probes(get_db())
