from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from ..auth import require_basic_auth
from ..checks.registry import DOMAINS, run_all, run_domain
from ..db import get_db

router = APIRouter(dependencies=[Depends(require_basic_auth)])


@router.get("/runs")
def list_runs(domain: str | None = None, limit: int = 50):
    conn = get_db()
    if domain:
        rows = conn.execute(
            "SELECT * FROM runs WHERE domain = ? ORDER BY started_at DESC LIMIT ?", (domain, limit)
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM runs ORDER BY started_at DESC LIMIT ?", (limit,)).fetchall()
    columns = [c[0] for c in conn.execute("SELECT * FROM runs LIMIT 0").description]
    return [dict(zip(columns, row)) for row in rows]


@router.get("/runs/latest")
def latest_per_domain():
    conn = get_db()
    result = {}
    for domain in DOMAINS:
        row = conn.execute(
            "SELECT * FROM runs WHERE domain = ? ORDER BY started_at DESC LIMIT 1", (domain,)
        ).fetchone()
        if row:
            columns = [c[0] for c in conn.execute("SELECT * FROM runs LIMIT 0").description]
            result[domain] = dict(zip(columns, row))
        else:
            result[domain] = None
    return result


@router.post("/runs/{domain}")
def trigger_run(domain: str):
    if domain == "all":
        return run_all(get_db())
    if domain not in DOMAINS:
        raise HTTPException(status_code=404, detail=f"unknown domain: {domain}")
    return run_domain(domain, get_db())
