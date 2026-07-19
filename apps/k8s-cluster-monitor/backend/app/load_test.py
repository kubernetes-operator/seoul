"""Active load-test feature.

Deliberately NOT a check "domain" — never added to checks/registry.py's
DOMAINS, so it can never be picked up by the cron scheduler or the "run all
domains" button. It gets its own route (api/routes_loadtest.py) and is only
ever invoked by an explicit human action, mirroring how the "proposed chaos
tests" tab is kept structurally inert elsewhere in this app.

Load is generated entirely in-process (httpx.AsyncClient + an
asyncio.Semaphore-equivalent worker-pool pattern) rather than by spawning a
separate Job/Pod. Spawning a Job would need `create` on pods/batch/v1 jobs —
a categorically bigger RBAC ask than anything else this app has ever
requested (this app's ClusterRole has zero create/update/patch/delete verbs,
anywhere, full stop — see deploy/k8s/02-clusterrole.yaml). A caller with
pod/job-create can run arbitrary images with arbitrary commands anywhere in
the cluster; that is strictly worse than the `secrets` read access this
codebase already refused elsewhere, so it's off the table here too.

Every parameter is capped SERVER-SIDE (not just suggested in the UI), the
target is restricted to a small hardcoded allow-list of in-cluster
Services (never an arbitrary hostname or an external URL — the egress
CiliumNetworkPolicy structurally can't reach the internet from this pod
anyway, see deploy/k8s/04-networkpolicy.yaml), only GET is supported, and
only one load test can run at a time (a threading.Lock, not asyncio.Lock —
this needs to be checked from both the scheduler's background thread and
this module's async route handler, and asyncio.Lock can't safely cross
that boundary).
"""
from __future__ import annotations

import asyncio
import threading
import time
from dataclasses import dataclass
from typing import Optional

import httpx

from .checks.base import CheckResult, Severity
from .config import settings

# Single source of truth for what can be targeted: namespace -> {service: [allowed ports]}.
# Keep in sync with deploy/k8s/04-networkpolicy.yaml's egress rules AND
# config.py's loadtest_allowed_namespaces (cross-reference, not auto-synced —
# this map is small enough that manual sync is fine, but a comment lives in
# both places). Ports were confirmed live via `kubectl get svc -n <ns>`, not
# guessed — this app's NetworkPolicy has a cautionary history about that.
ALLOWED_TARGETS: dict[str, dict[str, list[int]]] = {
    "demo-app": {"web": [80], "api": [8000]},
    "gitea": {"gitea-http": [3000]},
    "model-factory": {"model-portal": [8080]},
    "os-monitor": {"os-monitor-api": [8000], "os-monitor-dashboard": [80]},
}

P95_WARNING_MS = 1000.0
ERROR_RATE_WARNING = 0.05

_lock = threading.Lock()


class LoadTestError(Exception):
    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.message = message


@dataclass
class LoadTestParams:
    namespace: str
    service: str
    port: int
    path: str = "/"
    concurrency: int = 5
    duration_seconds: float = 10.0


def validate_params(p: LoadTestParams) -> None:
    if not settings.loadtest_enabled:
        raise LoadTestError(503, "Load-test feature is disabled (MONITOR_LOADTEST_ENABLED=false)")

    services = ALLOWED_TARGETS.get(p.namespace)
    if not services:
        raise LoadTestError(400, f"namespace '{p.namespace}' is not on the load-test allow-list")
    allowed_ports = services.get(p.service)
    if not allowed_ports:
        raise LoadTestError(400, f"service '{p.service}' is not allow-listed in namespace '{p.namespace}'")
    if p.port not in allowed_ports:
        raise LoadTestError(
            400, f"port {p.port} is not allow-listed for {p.namespace}/{p.service} (allowed: {allowed_ports})"
        )
    if not p.path.startswith("/"):
        raise LoadTestError(400, "path must start with '/'")
    if not (1 <= p.concurrency <= settings.loadtest_max_concurrency):
        raise LoadTestError(400, f"concurrency must be between 1 and {settings.loadtest_max_concurrency}")
    if not (0 < p.duration_seconds <= settings.loadtest_max_duration_seconds):
        raise LoadTestError(400, f"duration_seconds must be between 0 and {settings.loadtest_max_duration_seconds}")


def build_url(p: LoadTestParams) -> str:
    return f"http://{p.service}.{p.namespace}.svc.cluster.local:{p.port}{p.path}"


def estimate_summary(p: LoadTestParams) -> str:
    """Human-readable restatement for the frontend's confirm step."""
    max_possible = min(
        settings.loadtest_max_total_requests,
        int(p.concurrency * p.duration_seconds * 50),  # generous upper bound assuming 50 req/s per worker
    )
    return (
        f"concurrency {p.concurrency} × {p.duration_seconds:.0f}s × GET against "
        f"{p.namespace}/{p.service}:{p.port}{p.path} → up to ~{max_possible} requests"
    )


def _percentile(sorted_values: list[float], pct: float) -> Optional[float]:
    if not sorted_values:
        return None
    idx = min(len(sorted_values) - 1, int(pct * len(sorted_values)))
    return sorted_values[idx]


def _ms(seconds: Optional[float]) -> Optional[float]:
    return round(seconds * 1000, 1) if seconds is not None else None


async def _worker(client: httpx.AsyncClient, url: str, deadline: float, max_total: int, state: dict) -> None:
    while True:
        # No lock needed for this counter: asyncio is single-threaded/cooperative
        # and there's no `await` between the check and the increment, so no
        # other coroutine can interleave here.
        if time.monotonic() >= deadline or state["total"] >= max_total:
            return
        state["total"] += 1
        start = time.monotonic()
        try:
            resp = await client.get(url)
            state["latencies"].append(time.monotonic() - start)
            if resp.status_code >= 400:
                state["errors"] += 1
        except Exception:  # noqa: BLE001 - a network error is a failed request, not a crash
            state["latencies"].append(time.monotonic() - start)
            state["errors"] += 1


async def execute(p: LoadTestParams, triggered_by: str) -> dict:
    """Raises LoadTestError(400/503) on invalid/disabled params, (409) if
    another load test is already in flight."""
    validate_params(p)

    if not _lock.acquire(blocking=False):
        raise LoadTestError(409, "A load test is already running — try again after it finishes")

    started_at = time.time()
    url = build_url(p)
    state = {"total": 0, "errors": 0, "latencies": []}

    try:
        deadline = time.monotonic() + p.duration_seconds
        async with httpx.AsyncClient(timeout=settings.http_timeout_seconds) as client:
            await asyncio.gather(*[
                _worker(client, url, deadline, settings.loadtest_max_total_requests, state)
                for _ in range(p.concurrency)
            ])
    finally:
        _lock.release()

    finished_at = time.time()
    wall_seconds = finished_at - started_at
    latencies_sorted = sorted(state["latencies"])

    return {
        "target": url,
        "namespace": p.namespace,
        "concurrency": p.concurrency,
        "duration_seconds": p.duration_seconds,
        "total_requests": state["total"],
        "error_count": state["errors"],
        "p50_ms": _ms(_percentile(latencies_sorted, 0.50)),
        "p95_ms": _ms(_percentile(latencies_sorted, 0.95)),
        "p99_ms": _ms(_percentile(latencies_sorted, 0.99)),
        "achieved_rps": round(state["total"] / wall_seconds, 2) if wall_seconds > 0 else 0.0,
        "triggered_by": triggered_by,
        "status": "completed",
        "status_detail": None,
        "started_at": started_at,
        "finished_at": finished_at,
    }


def build_summary_check_result(result: dict) -> CheckResult:
    """One Finding per run, through the existing Finding/CheckResult shape,
    so results are visible anywhere findings/runs are already queried —
    without distorting that table's shape for every other domain (the raw
    metrics live in the dedicated load_tests table, see models.py).
    """
    cr = CheckResult("load_test")
    total = result["total_requests"]
    error_rate = (result["error_count"] / total) if total else 0.0
    target = result["target"]

    flagged = False
    if error_rate > ERROR_RATE_WARNING:
        flagged = True
        cr.add(
            "load_test_high_error_rate", Severity.WARNING,
            f"부하테스트 {target}: 에러율 {error_rate:.0%}",
            f"{result['total_requests']}건 중 {result['error_count']}건 실패 (concurrency={result['concurrency']}, "
            f"{result['duration_seconds']:.0f}s)",
            namespace=result["namespace"], resource=target,
        )
    if result["p95_ms"] is not None and result["p95_ms"] > P95_WARNING_MS:
        flagged = True
        cr.add(
            "load_test_high_p95_latency", Severity.WARNING,
            f"부하테스트 {target}: p95 응답시간 {result['p95_ms']:.0f}ms",
            f"{P95_WARNING_MS:.0f}ms 초과 (achieved_rps={result['achieved_rps']})",
            namespace=result["namespace"], resource=target,
        )
    if not flagged:
        cr.add(
            "load_test_completed", Severity.INFO,
            f"부하테스트 완료: {target}",
            f"{total}건 요청, 에러 {result['error_count']}건, p95={result['p95_ms']}ms, "
            f"achieved_rps={result['achieved_rps']} (triggered_by={result['triggered_by']})",
            namespace=result["namespace"], resource=target,
        )
    return cr
