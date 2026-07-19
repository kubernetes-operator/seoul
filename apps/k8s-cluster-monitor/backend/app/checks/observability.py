"""Observability stack checks — ported from
.claude/skills/k8s-observability-check/SKILL.md.

Runs from INSIDE the cluster, so it reaches Prometheus/Alertmanager/Grafana/
Loki via ClusterIP Service DNS directly — no port-forward needed (that was
only ever a workaround for the manual audit running outside the cluster).
Exact Service names are configurable (config.py / ConfigMap) since Helm
release naming varies.
"""
from __future__ import annotations

import httpx

from ..config import settings
from ..k8s_client import ClusterContext
from .base import CheckResult, Severity, safe_call


def run(ctx: ClusterContext) -> CheckResult:
    result = CheckResult("observability")

    _check_prometheus_and_alertmanager(result)
    _check_grafana(result)
    _check_loki(result)
    _check_scrape_coverage(ctx, result)

    return result


def _http_get(result: CheckResult, description: str, url: str, params: dict | None = None):
    try:
        resp = httpx.get(url, params=params, timeout=settings.http_timeout_seconds)
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:  # noqa: BLE001
        result.degrade(f"{description}: {exc.__class__.__name__} ({url})")
        return None


def _check_prometheus_and_alertmanager(result: CheckResult) -> None:
    targets = _http_get(result, "Prometheus targets", f"{settings.prometheus_url}/api/v1/targets")
    if targets:
        for t in targets.get("data", {}).get("activeTargets", []):
            if t.get("health") != "up":
                result.add(
                    "prometheus_target_down", Severity.WARNING,
                    f"타겟 다운: {t.get('labels', {}).get('job')} / {t.get('labels', {}).get('instance')}",
                    t.get("lastError", ""),
                )

    alerts = _http_get(result, "Prometheus alerts", f"{settings.prometheus_url}/api/v1/alerts")
    firing = []
    if alerts:
        firing = [a for a in alerts.get("data", {}).get("alerts", []) if a.get("state") == "firing"]
        for a in firing:
            labels = a.get("labels", {})
            if labels.get("alertname") in ("Watchdog", "InfoInhibitor"):
                continue
            result.add(
                "alert_firing", Severity.WARNING,
                f"Firing: {labels.get('alertname')} ({labels.get('severity', 'unknown')})",
                a.get("annotations", {}).get("summary", ""),
            )

    am_status = _http_get(result, "Prometheus->Alertmanager link", f"{settings.prometheus_url}/api/v1/alertmanagers")
    if am_status is not None:
        active = am_status.get("data", {}).get("activeAlertmanagers", [])
        if not active:
            result.add(
                "alertmanager_disconnected", Severity.CRITICAL,
                "Prometheus에 연결된 Alertmanager가 0개",
                f"발화 중인 알림({len(firing)}건 포함)이 어디로도 전달되지 않는 침묵 실패 상태. "
                "Alertmanager StatefulSet/CR 상태 확인 필요.",
            )


def _check_grafana(result: CheckResult) -> None:
    health = _http_get(result, "Grafana health", f"{settings.grafana_url}/api/health")
    if health is not None and health.get("database") != "ok":
        result.add(
            "grafana_unhealthy", Severity.WARNING, "Grafana health check 이상",
            f"응답: {health}",
        )


def _check_loki(result: CheckResult) -> None:
    import time

    now = time.time()
    query_result = _http_get(
        result, "Loki query_range", f"{settings.loki_url}/loki/api/v1/query_range",
        params={"query": '{namespace=~".+"}', "limit": 1, "start": int((now - 3600) * 1e9), "end": int(now * 1e9)},
    )
    if query_result is not None:
        results = query_result.get("data", {}).get("result", [])
        if not results:
            result.add(
                "loki_no_recent_logs", Severity.WARNING,
                "최근 1시간 내 Loki에 수집된 로그 없음",
                "인제스천 중단 가능성 — promtail/loki 파드 상태 확인 필요",
            )


def _check_scrape_coverage(ctx: ClusterContext, result: CheckResult) -> None:
    service_monitors = safe_call(
        result, "list ServiceMonitors", ctx.list_cluster_custom, "monitoring.coreos.com", "v1", "servicemonitors"
    )
    monitored_ns = {sm.get("metadata", {}).get("namespace") for sm in (service_monitors or [])}

    all_ns = {ns.metadata.name for ns in ctx.namespaces}
    system_ns = {n for n in all_ns if n.startswith("kube-")}
    uncovered = sorted(all_ns - monitored_ns - system_ns)
    if uncovered:
        result.add(
            "scrape_coverage_gap", Severity.INFO,
            f"ServiceMonitor 없는 네임스페이스 {len(uncovered)}개",
            "해당 네임스페이스의 애플리케이션 자체 메트릭은 수집되지 않음 (노드/파드 리소스 메트릭은 kubelet/"
            "node-exporter로 별도 커버): " + ", ".join(uncovered),
        )
