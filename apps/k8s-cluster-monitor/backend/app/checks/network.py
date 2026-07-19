"""Networking checks — ported from .claude/skills/k8s-network-check/SKILL.md.

Deliberately does NOT create ephemeral debug pods for live connectivity
tests (unlike the prose skill run manually by a human) — this tool's
ServiceAccount has zero create/exec/portforward verbs by design (see
deploy/k8s/02-clusterrole.yaml), so network health is inferred from
object state only (Service/Endpoints/NetworkPolicy/Gateway).
"""
from __future__ import annotations

from ..k8s_client import ClusterContext
from .base import CheckResult, Severity, safe_call


def run(ctx: ClusterContext) -> CheckResult:
    result = CheckResult("network")

    _check_cilium(ctx, result)
    _check_network_policy_coverage(ctx, result)
    _check_loadbalancer_metallb(ctx, result)
    _check_ingress_gateway(ctx, result)

    result.add(
        "connectivity_tests_excluded", Severity.INFO,
        "실시간 연결성 테스트는 이 도구에서 수행하지 않음",
        "임시 디버그 파드 생성에는 create 권한이 필요하며, 이 도구의 ServiceAccount는 읽기 전용(get/list/watch)만 보유합니다. "
        "실제 연결성 테스트가 필요하면 k8s-network-check 스킬(Claude Code)을 사용하세요.",
    )
    return result


def _check_cilium(ctx: ClusterContext, result: CheckResult) -> None:
    cilium_pods = [p for p in ctx.pods if p.metadata.namespace == "kube-system" and p.metadata.labels
                   and p.metadata.labels.get("k8s-app") == "cilium"]
    not_running = [p for p in cilium_pods if p.status.phase != "Running"]
    if cilium_pods and not_running:
        for p in not_running:
            result.add(
                "cilium_agent_unhealthy", Severity.CRITICAL,
                f"cilium 에이전트 {p.metadata.name}: phase={p.status.phase}",
                "노드의 CNI 에이전트가 비정상 — 해당 노드 네트워킹 전체에 영향 가능",
                namespace="kube-system", resource=p.metadata.name,
            )
    elif not cilium_pods:
        result.add(
            "cilium_not_found", Severity.INFO, "Cilium DaemonSet(k8s-app=cilium) 미발견",
            "다른 CNI를 사용 중이거나 라벨이 다를 수 있음",
        )


def _check_network_policy_coverage(ctx: ClusterContext, result: CheckResult) -> None:
    policies = safe_call(result, "list NetworkPolicies", ctx.networking.list_network_policy_for_all_namespaces)
    covered_ns = {p.metadata.namespace for p in policies.items} if policies else set()

    cilium_policies = safe_call(
        result, "list CiliumNetworkPolicies", ctx.list_cluster_custom, "cilium.io", "v2", "ciliumnetworkpolicies"
    )
    if cilium_policies:
        covered_ns |= {p.get("metadata", {}).get("namespace") for p in cilium_policies}

    all_ns = {ns.metadata.name for ns in ctx.namespaces}
    system_ns = {n for n in all_ns if n.startswith("kube-") or n in ("default",)}
    uncovered_user_ns = sorted(all_ns - covered_ns - system_ns)

    if uncovered_user_ns:
        result.add(
            "networkpolicy_coverage_gap", Severity.INFO,
            f"NetworkPolicy 없는 사용자 네임스페이스 {len(uncovered_user_ns)}개",
            "정책 부재 = 기본 all-allow. 의도된 것인지 확인 필요 (취약점 단정 아님): " + ", ".join(uncovered_user_ns),
            reproduce_cmd="kubectl get networkpolicy,ciliumnetworkpolicy -A",
        )


def _check_loadbalancer_metallb(ctx: ClusterContext, result: CheckResult) -> None:
    services = safe_call(result, "list services", ctx.core.list_service_for_all_namespaces)
    for svc in (services.items if services else []):
        if svc.spec.type != "LoadBalancer":
            continue
        ingress = (svc.status.load_balancer.ingress or []) if svc.status.load_balancer else []
        if not ingress:
            result.add(
                "loadbalancer_pending", Severity.WARNING,
                f"Service {svc.metadata.namespace}/{svc.metadata.name}: External-IP 할당 대기(pending)",
                "MetalLB IPAddressPool 고갈 또는 L2Advertisement 미매칭 가능성",
                namespace=svc.metadata.namespace, resource=svc.metadata.name,
                reproduce_cmd="kubectl get ipaddresspool,l2advertisement -n metallb-system",
            )

    pools = safe_call(result, "list MetalLB IPAddressPools", ctx.list_namespaced_custom,
                       "metallb.io", "v1beta1", "metallb-system", "ipaddresspools")
    if pools is not None and not pools:
        result.add(
            "metallb_no_pools", Severity.WARNING, "MetalLB IPAddressPool 없음",
            "LoadBalancer 타입 Service가 있다면 IP 할당이 불가능할 수 있음",
        )


def _check_ingress_gateway(ctx: ClusterContext, result: CheckResult) -> None:
    routes = safe_call(result, "list HTTPRoutes", ctx.list_cluster_custom,
                        "gateway.networking.k8s.io", "v1", "httproutes")
    services = safe_call(result, "list services", ctx.core.list_service_for_all_namespaces)
    endpoints = safe_call(result, "list endpoints", ctx.core.list_endpoints_for_all_namespaces)
    endpoints_with_addresses = set()
    if endpoints:
        for ep in endpoints.items:
            if any((subset.addresses for subset in (ep.subsets or []) if subset.addresses)):
                endpoints_with_addresses.add((ep.metadata.namespace, ep.metadata.name))

    for route in (routes or []):
        meta = route.get("metadata", {})
        ns = meta.get("namespace")
        name = meta.get("name")
        status = route.get("status", {})
        for parent in status.get("parents", []):
            for cond in parent.get("conditions", []):
                if cond.get("type") in ("Accepted", "ResolvedRefs") and cond.get("status") != "True":
                    result.add(
                        "httproute_not_ready", Severity.WARNING,
                        f"HTTPRoute {ns}/{name}: {cond.get('type')}=False",
                        cond.get("message", ""), namespace=ns, resource=name,
                        reproduce_cmd=f"kubectl -n {ns} describe httproute {name}",
                    )
        for rule in route.get("spec", {}).get("rules", []):
            for backend in rule.get("backendRefs", []):
                backend_name = backend.get("name")
                if backend_name and (ns, backend_name) not in endpoints_with_addresses:
                    result.add(
                        "httproute_backend_no_endpoints", Severity.WARNING,
                        f"HTTPRoute {ns}/{name} 백엔드 {backend_name}에 엔드포인트 없음",
                        "트래픽을 받을 준비된 파드가 없음 (Service 셀렉터 불일치 또는 파드 다운 가능)",
                        namespace=ns, resource=backend_name,
                    )

    certs = safe_call(result, "list Certificates", ctx.list_cluster_custom, "cert-manager.io", "v1", "certificates")
    for cert in (certs or []):
        status = cert.get("status", {})
        ready = next((c for c in status.get("conditions", []) if c.get("type") == "Ready"), None)
        if ready and ready.get("status") != "True":
            meta = cert.get("metadata", {})
            result.add(
                "certificate_not_ready", Severity.WARNING,
                f"Certificate {meta.get('namespace')}/{meta.get('name')}: Ready=False",
                ready.get("message", ""), namespace=meta.get("namespace"), resource=meta.get("name"),
            )
