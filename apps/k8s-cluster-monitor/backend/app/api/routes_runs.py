from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from ..auth import require_session, visible_domains
from ..checks.registry import DOMAINS, run_all, run_domain
from ..db import get_db

router = APIRouter(dependencies=[Depends(require_session)])


@router.get("/runs")
def list_runs(domain: str | None = None, limit: int = 50,current: str=Depends(require_session)):
    conn=get_db();query='SELECT * FROM runs WHERE 1=1';params=[]
    if domain:query+=' AND domain=?';params.append(domain)
    allowed=visible_domains(current)
    if allowed is not None:
        if not allowed:return []
        query+=' AND domain IN ('+','.join('?' for _ in allowed)+')';params.extend(allowed)
    query+=' ORDER BY started_at DESC LIMIT ?';params.append(limit)
    rows=conn.execute(query,params).fetchall()
    columns=[c[0] for c in conn.execute('SELECT * FROM runs LIMIT 0').description]
    return [dict(zip(columns,row)) for row in rows]


@router.get("/runs/latest")
def latest_per_domain(current: str=Depends(require_session)):
    conn = get_db()
    result = {}
    allowed=visible_domains(current)
    for domain in DOMAINS:
        if allowed is not None and domain not in allowed:continue
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
