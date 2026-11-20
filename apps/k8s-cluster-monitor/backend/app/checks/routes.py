"""Domains & pages — auto-discovers every HTTPRoute hostname/path and probes it.

Unlike network.py (object state only), this domain makes a real HTTPS GET
per discovered (hostname, path) — but NOT over the internet. Each probe goes
to the cluster's own Gateway data plane Service (settings.routes_gateway_host)
with the route's hostname in both the Host header and TLS SNI, so it
exercises exactly the gateway -> route -> backend path a browser would hit,
minus public DNS / CDN. That keeps the egress CiliumNetworkPolicy narrow
(one namespace, one port) instead of opening the pod to the internet.

Read-only by construction: GET only, redirects not followed, no request body,
no credentials sent. A 401/403 therefore counts as "reachable" (the route and
backend answered; the page just needs a login) — only connection failures,
timeouts and 5xx are treated as broken.

New routes are picked up automatically on the next run (no allow-list to
maintain); the UI flags routes created within NEW_ROUTE_DAYS as "NEW".
"""
from __future__ import annotations

import socket
import ssl
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Callable, Optional

import httpx

from ..config import settings
from ..k8s_client import ClusterContext
from .base import CheckResult, Severity, safe_call

NEW_ROUTE_DAYS = 7
_MAX_WORKERS = 8


@dataclass
class Target:
    host: str
    path: str
    namespace: str
    route: str
    route_created: Optional[float] = None
    accepted: Optional[bool] = None
    skip_reason: Optional[str] = None


@dataclass
class ProbeRow:
    host: str
    path: str
    namespace: str
    route: str
    route_created: Optional[float]
    accepted: Optional[bool]
    state: str  # ok | auth | client_error | server_error | unreachable | skipped
    status_code: Optional[int] = None
    latency_ms: Optional[float] = None
    error: Optional[str] = None
    tls_ok: Optional[bool] = None
    tls_error: Optional[str] = None
    cert_not_after: Optional[float] = None


@dataclass
class TlsInfo:
    ok: bool
    not_after: Optional[float] = None
    error: Optional[str] = None


@dataclass
class HttpInfo:
    status_code: Optional[int] = None
    latency_ms: Optional[float] = None
    error: Optional[str] = None


def _parse_ts(value: Optional[str]) -> Optional[float]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def discover_targets(routes: list[dict]) -> list[Target]:
    """One Target per unique (hostname, path). Matches the probe can't
    faithfully reproduce (regex paths, header/query/method constraints,
    wildcard or missing hostnames) are kept as skipped rows rather than
    silently dropped, so the page still shows the route exists."""
    targets: dict[tuple[str, str], Target] = {}
    for route in routes:
        meta = route.get("metadata", {})
        ns, name = meta.get("namespace", ""), meta.get("name", "")
        created = _parse_ts(meta.get("creationTimestamp"))
        conds = [c for p in route.get("status", {}).get("parents", []) for c in p.get("conditions", [])]
        acc = [c for c in conds if c.get("type") == "Accepted"]
        accepted = all(c.get("status") == "True" for c in acc) if acc else None
        hosts = route.get("spec", {}).get("hostnames") or []

        paths: list[tuple[str, Optional[str]]] = []
        for rule in route.get("spec", {}).get("rules", []) or [{}]:
            for match in rule.get("matches") or [{"path": {"type": "PathPrefix", "value": "/"}}]:
                p = match.get("path") or {"type": "PathPrefix", "value": "/"}
                value = p.get("value") or "/"
                skip = None
                if p.get("type") == "RegularExpression":
                    skip = "정규식 경로 — 실측 생략"
                elif match.get("headers") or match.get("queryParams"):
                    skip = "헤더/쿼리 조건 경로 — 실측 생략"
                elif match.get("method") not in (None, "GET"):
                    skip = f"{match.get('method')} 전용 경로 — 실측 생략"
                paths.append((value, skip))

        if not hosts:
            hosts = [""]
        for host in hosts:
            for value, skip in paths:
                key = (host, value)
                if key in targets:
                    continue
                if not host:
                    skip = "hostname 미지정 — 실측 생략"
                elif "*" in host:
                    skip = "와일드카드 hostname — 실측 생략"
                targets[key] = Target(host, value, ns, name, created, accepted, skip)
    return sorted(targets.values(), key=lambda t: (t.host, t.path))


def probe_http(host: str, path: str) -> HttpInfo:
    url = f"https://{settings.routes_gateway_host}:{settings.routes_gateway_https_port}{path}"
    start = time.perf_counter()
    try:
        # verify=False here on purpose: certificate validity is checked
        # separately (probe_tls) so a cert problem is reported as its own
        # finding instead of masking whether the page itself responds.
        with httpx.Client(verify=False, follow_redirects=False,
                          timeout=settings.routes_probe_timeout_seconds) as client:
            resp = client.get(url, headers={"Host": host, "User-Agent": "k8s-cluster-tester/route-probe"},
                              extensions={"sni_hostname": host})
        return HttpInfo(resp.status_code, (time.perf_counter() - start) * 1000)
    except httpx.TimeoutException:
        return HttpInfo(None, (time.perf_counter() - start) * 1000, "timeout")
    except Exception as exc:  # noqa: BLE001 - any transport error = unreachable
        return HttpInfo(None, None, exc.__class__.__name__)


def probe_tls(host: str) -> TlsInfo:
    ctx = ssl.create_default_context()
    try:
        with socket.create_connection((settings.routes_gateway_host, settings.routes_gateway_https_port),
                                      timeout=settings.routes_probe_timeout_seconds) as sock:
            with ctx.wrap_socket(sock, server_hostname=host) as tls:
                cert = tls.getpeercert()
        return TlsInfo(True, ssl.cert_time_to_seconds(cert["notAfter"]))
    except ssl.SSLCertVerificationError as exc:
        return TlsInfo(False, None, exc.verify_message or "인증서 검증 실패")
    except Exception as exc:  # noqa: BLE001
        return TlsInfo(False, None, exc.__class__.__name__)


def classify(code: Optional[int]) -> str:
    if code is None:
        return "unreachable"
    if code >= 500:
        return "server_error"
    if code in (401, 403):
        return "auth"
    if code >= 400:
        return "client_error"
    return "ok"


def run(ctx: ClusterContext, *, http_probe: Callable[[str, str], HttpInfo] = probe_http,
        tls_probe: Callable[[str], TlsInfo] = probe_tls) -> CheckResult:
    result = CheckResult("routes")
    routes = safe_call(result, "list HTTPRoutes", ctx.list_cluster_custom,
                       "gateway.networking.k8s.io", "v1", "httproutes")
    if routes is None:
        result.probes = []
        return result

    targets = discover_targets(routes)
    live = [t for t in targets if not t.skip_reason]
    hosts = sorted({t.host for t in live})

    with ThreadPoolExecutor(max_workers=_MAX_WORKERS) as pool:
        tls_by_host = dict(zip(hosts, pool.map(tls_probe, hosts)))
        http_by_target = dict(zip([(t.host, t.path) for t in live],
                                  pool.map(lambda t: http_probe(t.host, t.path), live)))

    rows: list[ProbeRow] = []
    for t in targets:
        base = dict(host=t.host, path=t.path, namespace=t.namespace, route=t.route,
                    route_created=t.route_created, accepted=t.accepted)
        if t.skip_reason:
            rows.append(ProbeRow(**base, state="skipped", error=t.skip_reason))
            continue
        h = http_by_target[(t.host, t.path)]
        tls = tls_by_host[t.host]
        rows.append(ProbeRow(**base, state=classify(h.status_code), status_code=h.status_code,
                             latency_ms=h.latency_ms, error=h.error, tls_ok=tls.ok,
                             tls_error=tls.error, cert_not_after=tls.not_after))

    _findings(result, rows, tls_by_host)
    result.probes = [asdict(r) for r in rows]
    return result


def _findings(result: CheckResult, rows: list[ProbeRow], tls_by_host: dict[str, TlsInfo]) -> None:
    now = time.time()
    for r in rows:
        url = f"https://{r.host}{r.path}"
        cmd = (f"curl -sk -o /dev/null -w '%{{http_code}} %{{time_total}}s\\n' "
               f"--resolve {r.host}:443:<gateway-ip> {url}")
        resource = f"{r.host}{r.path}"
        if r.state == "unreachable":
            result.add("route_unreachable", Severity.CRITICAL, f"{url} 응답 없음 ({r.error})",
                       f"Gateway 경유 GET이 실패했습니다. HTTPRoute {r.namespace}/{r.route}의 백엔드 상태를 확인하세요.",
                       namespace=r.namespace, resource=resource, reproduce_cmd=cmd)
        elif r.state == "server_error":
            result.add("route_server_error", Severity.CRITICAL, f"{url} → HTTP {r.status_code}",
                       f"5xx 응답 — 백엔드 다운/오류 가능 (HTTPRoute {r.namespace}/{r.route}).",
                       namespace=r.namespace, resource=resource, reproduce_cmd=cmd)
        elif r.latency_ms and r.latency_ms > settings.routes_latency_warning_ms:
            result.add("route_slow", Severity.WARNING, f"{url} 응답 지연 {r.latency_ms:.0f}ms",
                       f"기준 {settings.routes_latency_warning_ms:.0f}ms 초과 (HTTP {r.status_code}).",
                       namespace=r.namespace, resource=resource,
                       evidence=f"latency_ms={r.latency_ms:.0f}", reproduce_cmd=cmd)
        if r.accepted is False:
            result.add("route_not_accepted", Severity.WARNING, f"HTTPRoute {r.namespace}/{r.route} 미승인",
                       "Gateway가 이 라우트를 Accepted 하지 않았습니다.", namespace=r.namespace,
                       resource=r.route, reproduce_cmd=f"kubectl -n {r.namespace} describe httproute {r.route}")

    for host, tls in tls_by_host.items():
        cmd = f"openssl s_client -connect <gateway-ip>:443 -servername {host} </dev/null | openssl x509 -noout -subject -enddate -ext subjectAltName"
        if not tls.ok:
            result.add("route_tls_invalid", Severity.WARNING, f"{host} TLS 인증서 검증 실패",
                       f"{tls.error} — 인증서 SAN에 이 hostname이 없거나 신뢰할 수 없는 인증서입니다. "
                       "브라우저에서 HTTPS 경고가 표시될 수 있습니다.", resource=host, reproduce_cmd=cmd)
        elif tls.not_after:
            days = (tls.not_after - now) / 86400
            if days < settings.routes_cert_warning_days:
                sev = Severity.CRITICAL if days < 7 else Severity.WARNING
                result.add("route_cert_expiring", sev, f"{host} 인증서 {days:.0f}일 후 만료",
                           f"만료일 {datetime.fromtimestamp(tls.not_after, timezone.utc):%Y-%m-%d} (UTC).",
                           resource=host, reproduce_cmd=cmd)
