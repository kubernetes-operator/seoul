"""Passive performance signals — read-only, via Prometheus HTTP API, same
pattern as observability.py (`_http_get` + `result.degrade()` on failure).
No new RBAC, no new NetworkPolicy egress: Prometheus is already reachable.

App-level latency/error-rate checks are opportunistic: most workloads in
this cluster were never instrumented with a Prometheus client library, so
we first ask "which namespaces even expose http_request_duration_seconds?"
and only check those. A namespace NOT exposing it is not a finding — unlike
ServiceMonitor coverage (an actionable gap, see observability.py), most
apps here simply were never written to export histograms.
"""
from __future__ import annotations

import httpx

from ..config import settings
from .base import CheckResult, Severity

CPU_THROTTLE_RATIO_WARNING = 0.25  # 25% of CFS periods throttled, sustained
MEMORY_NEAR_LIMIT_RATIO = 0.9
NODE_CPU_BUSY_WARNING = 0.85
NODE_MEM_AVAILABLE_WARNING = 0.10  # less than 10% memory available
NODE_DISK_AVAILABLE_WARNING = 0.10
P95_LATENCY_WARNING_SECONDS = 1.0
ERROR_RATE_WARNING = 0.05  # 5% of requests erroring


def run(ctx) -> CheckResult:  # ctx: ClusterContext, unused here but kept for registry's uniform signature
    result = CheckResult("performance")

    _check_cpu_throttling(result)
    _check_memory_pressure(result)
    _check_node_saturation(result)
    _check_app_latency_and_errors(result)

    return result


def _query(result: CheckResult, description: str, promql: str):
    try:
        resp = httpx.get(
            f"{settings.prometheus_url}/api/v1/query",
            params={"query": promql},
            timeout=settings.http_timeout_seconds,
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("data", {}).get("result", [])
    except Exception as exc:  # noqa: BLE001
        result.degrade(f"{description}: {exc.__class__.__name__}")
        return None


def _check_cpu_throttling(result: CheckResult) -> None:
    promql = (
        'rate(container_cpu_cfs_throttled_periods_total{container!="",container!="POD"}[5m]) '
        '/ rate(container_cpu_cfs_periods_total{container!="",container!="POD"}[5m]) > %s'
        % CPU_THROTTLE_RATIO_WARNING
    )
    series = _query(result, "CPU throttling", promql)
    for s in (series or []):
        labels = s.get("metric", {})
        ratio = float(s["value"][1])
        result.add(
            "cpu_throttling_high", Severity.WARNING,
            f"{labels.get('namespace')}/{labels.get('pod')} 컨테이너 {labels.get('container')}: CPU 스로틀링 {ratio:.0%}",
            "지속적인 CPU 스로틀링 — CPU limit 상향 또는 코드 최적화 검토",
            namespace=labels.get("namespace"), resource=labels.get("pod"),
        )


def _check_memory_pressure(result: CheckResult) -> None:
    promql = (
        'container_memory_working_set_bytes{container!="",container!="POD"} '
        '/ container_spec_memory_limit_bytes{container!="",container!="POD"} > %s '
        'and container_spec_memory_limit_bytes{container!="",container!="POD"} > 0'
        % MEMORY_NEAR_LIMIT_RATIO
    )
    series = _query(result, "memory pressure", promql)
    for s in (series or []):
        labels = s.get("metric", {})
        ratio = float(s["value"][1])
        result.add(
            "memory_near_limit", Severity.WARNING,
            f"{labels.get('namespace')}/{labels.get('pod')} 컨테이너 {labels.get('container')}: 메모리 사용률 {ratio:.0%}",
            "메모리 limit에 근접 — OOMKill 위험",
            namespace=labels.get("namespace"), resource=labels.get("pod"),
        )


def _check_node_saturation(result: CheckResult) -> None:
    cpu_busy = _query(
        result, "node CPU busy",
        '1 - avg by (instance) (rate(node_cpu_seconds_total{mode="idle"}[5m]))',
    )
    for s in (cpu_busy or []):
        ratio = float(s["value"][1])
        if ratio > NODE_CPU_BUSY_WARNING:
            instance = s.get("metric", {}).get("instance")
            result.add(
                "node_cpu_saturated", Severity.WARNING,
                f"노드 {instance}: CPU 사용률 {ratio:.0%}",
                "지속되면 스케줄링 지연/스로틀링 위험", resource=instance,
            )

    mem_avail = _query(
        result, "node memory available ratio",
        "node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes",
    )
    for s in (mem_avail or []):
        ratio = float(s["value"][1])
        if ratio < NODE_MEM_AVAILABLE_WARNING:
            instance = s.get("metric", {}).get("instance")
            result.add(
                "node_memory_saturated", Severity.WARNING,
                f"노드 {instance}: 가용 메모리 {ratio:.0%}",
                "가용 메모리 부족 — Evict/OOM 위험", resource=instance,
            )

    disk_avail = _query(
        result, "node disk available ratio",
        'node_filesystem_avail_bytes{mountpoint="/"} / node_filesystem_size_bytes{mountpoint="/"}',
    )
    for s in (disk_avail or []):
        ratio = float(s["value"][1])
        if ratio < NODE_DISK_AVAILABLE_WARNING:
            instance = s.get("metric", {}).get("instance")
            result.add(
                "node_disk_saturated", Severity.WARNING,
                f"노드 {instance}: 가용 디스크 {ratio:.0%}",
                "디스크 공간 부족 위험", resource=instance,
            )


def _check_app_latency_and_errors(result: CheckResult) -> None:
    instrumented = _query(
        result, "http_request_duration_seconds existence check",
        "count(http_request_duration_seconds_bucket) by (namespace)",
    )
    if not instrumented:
        return  # nothing exposes this metric — silently skip, not a finding

    namespaces = [s.get("metric", {}).get("namespace") for s in instrumented if s.get("metric", {}).get("namespace")]

    for ns in namespaces:
        p95_series = _query(
            result, f"p95 latency ({ns})",
            'histogram_quantile(0.95, sum by (le) (rate(http_request_duration_seconds_bucket{namespace="%s"}[5m])))' % ns,
        )
        for s in (p95_series or []):
            try:
                p95 = float(s["value"][1])
            except (KeyError, ValueError, TypeError):
                continue
            if p95 > P95_LATENCY_WARNING_SECONDS:
                result.add(
                    "app_p95_latency_high", Severity.WARNING,
                    f"{ns}: p95 응답시간 {p95:.2f}s",
                    f"{P95_LATENCY_WARNING_SECONDS}s 초과", namespace=ns,
                )

        error_series = _query(
            result, f"error rate ({ns})",
            'sum(rate(http_requests_total{namespace="%s",code=~"5.."}[5m])) '
            '/ sum(rate(http_requests_total{namespace="%s"}[5m]))' % (ns, ns),
        )
        for s in (error_series or []):
            try:
                error_rate = float(s["value"][1])
            except (KeyError, ValueError, TypeError):
                continue
            if error_rate > ERROR_RATE_WARNING:
                result.add(
                    "app_error_rate_high", Severity.WARNING,
                    f"{ns}: 5xx 에러율 {error_rate:.0%}",
                    f"{ERROR_RATE_WARNING:.0%} 초과", namespace=ns,
                )
