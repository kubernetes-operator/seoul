from __future__ import annotations

from fastapi import APIRouter, Depends

from ..auth import require_session
from ..db import get_db

router = APIRouter(dependencies=[Depends(require_session)])


@router.get("/findings")
def list_findings(domain: str | None = None, severity: str | None = None, include_resolved: bool = False):
    conn = get_db()
    query = "SELECT * FROM findings WHERE 1=1"
    params: list = []
    if domain:
        query += " AND domain = ?"
        params.append(domain)
    if severity:
        query += " AND severity = ?"
        params.append(severity)
    if not include_resolved:
        query += " AND resolved_at IS NULL"
    query += " ORDER BY severity, last_seen DESC"

    rows = conn.execute(query, params).fetchall()
    columns = [c[0] for c in conn.execute("SELECT * FROM findings LIMIT 0").description]
    return [dict(zip(columns, row)) for row in rows]


@router.get("/findings/trend")
def findings_trend(domain: str | None = None, days: int = 14):
    """Findings-over-time by severity, bucketed by day — powers the trend chart."""
    import time

    conn = get_db()
    cutoff = time.time() - days * 86400
    query = """
        SELECT domain, started_at, critical_count, warning_count, info_count
        FROM runs WHERE started_at >= ?
    """
    params: list = [cutoff]
    if domain:
        query += " AND domain = ?"
        params.append(domain)
    query += " ORDER BY started_at ASC"

    rows = conn.execute(query, params).fetchall()
    return [
        {"domain": r[0], "started_at": r[1], "critical": r[2], "warning": r[3], "info": r[4]}
        for r in rows
    ]
