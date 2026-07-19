"""Domain registry — maps a domain name to its check module's run()
function. Adding a new check domain means adding one module plus one entry
here; the API, scheduler, and frontend all iterate over this dict rather
than hardcoding domain names in more than one place.
"""
from __future__ import annotations

import time
from typing import Callable

from ..k8s_client import ClusterContext
from .base import CheckResult
from . import health, network, security, storage_backup, observability

DOMAINS: dict[str, Callable[[ClusterContext], CheckResult]] = {
    "health": health.run,
    "network": network.run,
    "security": security.run,
    "storage_backup": storage_backup.run,
    "observability": observability.run,
}


def run_domain(domain: str, conn) -> dict:
    from ..models import save_run  # local import: avoids a circular import at module load time

    if domain not in DOMAINS:
        raise ValueError(f"unknown domain: {domain}")

    ctx = ClusterContext()
    started_at = time.time()
    result = DOMAINS[domain](ctx)
    finished_at = time.time()

    run_id = save_run(conn, result, started_at, finished_at)
    return {
        "run_id": run_id,
        "domain": domain,
        "status": result.status,
        "status_reasons": result.status_reasons,
        "counts": result.summary_counts(),
        "started_at": started_at,
        "finished_at": finished_at,
    }


def run_all(conn) -> list[dict]:
    return [run_domain(d, conn) for d in DOMAINS]
