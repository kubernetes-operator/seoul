"""Node / control-plane / workload health checks — ported from
.claude/skills/k8s-health-check/SKILL.md. Read-only: get/list/watch only.
"""
from __future__ import annotations

import datetime
from collections import Counter

from ..k8s_client import ClusterContext
from .base import CheckResult, Severity, owner_name, safe_call

PRESSURE_CONDITIONS = ("MemoryPressure", "DiskPressure", "PIDPressure", "NetworkUnavailable")
CONTROL_PLANE_PREFIXES = ("kube-apiserver", "kube-scheduler", "kube-controller-manager", "etcd")


def run(ctx: ClusterContext) -> CheckResult:
    result = CheckResult("health")

    _check_nodes(ctx, result)
    _check_control_plane(ctx, result)
    _check_workloads(ctx, result)
    _check_hpa_pdb(ctx, result)
    _check_events(ctx, result)
    _check_resource_usage(ctx, result)

    return result


def _check_nodes(ctx: ClusterContext, result: CheckResult) -> None:
    nodes = safe_call(result, "list nodes", ctx.core.list_node)
    if not nodes:
        return
    for node in nodes.items:
        name = node.metadata.name
        conditions = {c.type: c.status for c in (node.status.conditions or [])}
        if conditions.get("Ready") != "True":
            result.add(
                "node_not_ready", Severity.CRITICAL, f"노드 {name} Not Ready",
                f"Ready condition status={conditions.get('Ready', 'Unknown')}",
                resource=name, reproduce_cmd=f"kubectl describe node {name}",
            )
        for cond in PRESSURE_CONDITIONS:
            if conditions.get(cond) == "True":
                result.add(
                    "node_pressure", Severity.CRITICAL, f"노드 {name}: {cond}",
                    f"{cond} condition = True — 리소스 압박 상태", resource=name,
                    reproduce_cmd=f"kubectl describe node {name}",
                )


def _check_control_plane(ctx: ClusterContext, result: CheckResult) -> None:
    kube_system_pods = [p for p in ctx.pods if p.metadata.namespace == "kube-system"]
    for prefix in CONTROL_PLANE_PREFIXES:
        matched = [p for p in kube_system_pods if p.metadata.name.startswith(prefix)]
        if not matched:
            result.add(
                "control_plane_not_exposed", Severity.INFO,
                f"{prefix} 정적 파드 미노출", "매니지드 컨트롤플레인이거나 외부/systemd 관리 구성으로 추정 — 호스트 레벨 확인 필요",
            )
            continue
        for pod in matched:
            if pod.status.phase != "Running":
                result.add(
                    "control_plane_unhealthy", Severity.CRITICAL,
                    f"{pod.metadata.name} phase={pod.status.phase}",
                    "컨트롤플레인 컴포넌트 파드가 Running 상태가 아님",
                    namespace="kube-system", resource=pod.metadata.name,
                    reproduce_cmd=f"kubectl -n kube-system describe pod {pod.metadata.name}",
                )


def _check_workloads(ctx: ClusterContext, result: CheckResult) -> None:
    deployments = safe_call(result, "list deployments", ctx.apps.list_deployment_for_all_namespaces)
    for dep in (deployments.items if deployments else []):
        desired = dep.spec.replicas or 0
        ready = dep.status.ready_replicas or 0
        if desired > 0 and ready < desired:
            result.add(
                "deployment_not_ready", Severity.WARNING,
                f"Deployment {dep.metadata.namespace}/{dep.metadata.name}: {ready}/{desired} ready",
                "준비된 레플리카 수가 목표치에 미달", namespace=dep.metadata.namespace, resource=dep.metadata.name,
                reproduce_cmd=f"kubectl -n {dep.metadata.namespace} describe deploy {dep.metadata.name}",
            )

    statefulsets = safe_call(result, "list statefulsets", ctx.apps.list_stateful_set_for_all_namespaces)
    for sts in (statefulsets.items if statefulsets else []):
        desired = sts.spec.replicas or 0
        ready = sts.status.ready_replicas or 0
        if desired > 0 and ready < desired:
            result.add(
                "statefulset_not_ready", Severity.WARNING,
                f"StatefulSet {sts.metadata.namespace}/{sts.metadata.name}: {ready}/{desired} ready",
                "준비된 레플리카 수가 목표치에 미달", namespace=sts.metadata.namespace, resource=sts.metadata.name,
                reproduce_cmd=f"kubectl -n {sts.metadata.namespace} describe sts {sts.metadata.name}",
            )

    daemonsets = safe_call(result, "list daemonsets", ctx.apps.list_daemon_set_for_all_namespaces)
    for ds in (daemonsets.items if daemonsets else []):
        desired = ds.status.desired_number_scheduled or 0
        ready = ds.status.number_ready or 0
        if desired > 0 and ready < desired:
            result.add(
                "daemonset_not_ready", Severity.WARNING,
                f"DaemonSet {ds.metadata.namespace}/{ds.metadata.name}: {ready}/{desired} ready",
                "준비된 파드 수가 목표치에 미달", namespace=ds.metadata.namespace, resource=ds.metadata.name,
                reproduce_cmd=f"kubectl -n {ds.metadata.namespace} describe ds {ds.metadata.name}",
            )

    # Restart-count is a chronic-pattern signal (unlike pod_unhealthy below, which
    # is "look at this specific instance right now") — dedupe by owning workload +
    # container so a Deployment with several replicas restarting doesn't produce
    # one finding per replica, and only escalate to WARNING when the most recent
    # restart was actually recent (old, long-stable restart history is INFO, not
    # an active issue — matches the human audit's "누적치, 활성 이슈 아님" judgment).
    seen_restart_keys: set[tuple[str, str, str]] = set()
    now = datetime.datetime.now(datetime.timezone.utc)

    for pod in ctx.pods:
        phase = pod.status.phase
        if phase not in ("Running", "Succeeded"):
            result.add(
                "pod_unhealthy", Severity.CRITICAL,
                f"Pod {pod.metadata.namespace}/{pod.metadata.name}: phase={phase}",
                "Running/Succeeded 상태가 아님", namespace=pod.metadata.namespace, resource=pod.metadata.name,
                reproduce_cmd=f"kubectl -n {pod.metadata.namespace} describe pod {pod.metadata.name}",
            )
            continue
        owner = owner_name(pod)
        for cs in (pod.status.container_statuses or []):
            if not (cs.restart_count and cs.restart_count > 3):
                continue
            key = (pod.metadata.namespace, owner, cs.name)
            if key in seen_restart_keys:
                continue
            seen_restart_keys.add(key)

            last_restart_recent = False
            if cs.last_state and cs.last_state.terminated and cs.last_state.terminated.finished_at:
                age = now - cs.last_state.terminated.finished_at
                last_restart_recent = age < datetime.timedelta(hours=24)

            severity = Severity.WARNING if last_restart_recent else Severity.INFO
            recency_note = "최근 24시간 내 재시작 발생" if last_restart_recent else "최근 재시작 없음 — 누적 이력, 현재 안정 가능성"
            result.add(
                "pod_high_restarts", severity,
                f"{pod.metadata.namespace}/{owner} 컨테이너 {cs.name}: 재시작 {cs.restart_count}회",
                f"{recency_note}",
                namespace=pod.metadata.namespace, resource=owner,
                reproduce_cmd=f"kubectl -n {pod.metadata.namespace} logs {pod.metadata.name} -c {cs.name} --previous",
            )


def _check_hpa_pdb(ctx: ClusterContext, result: CheckResult) -> None:
    hpas = safe_call(result, "list HPAs", ctx.autoscaling.list_horizontal_pod_autoscaler_for_all_namespaces)
    for hpa in (hpas.items if hpas else []):
        if hpa.status.current_replicas == hpa.spec.max_replicas:
            result.add(
                "hpa_maxed_out", Severity.WARNING,
                f"HPA {hpa.metadata.namespace}/{hpa.metadata.name} 최대치 도달",
                f"currentReplicas가 maxReplicas({hpa.spec.max_replicas})와 동일 — 용량 부족 가능성",
                namespace=hpa.metadata.namespace, resource=hpa.metadata.name,
            )

    pdbs = safe_call(result, "list PDBs", ctx.policy.list_pod_disruption_budget_for_all_namespaces)
    for pdb in (pdbs.items if pdbs else []):
        if (pdb.status.disruptions_allowed or 0) == 0:
            result.add(
                "pdb_zero_disruptions_allowed", Severity.INFO,
                f"PDB {pdb.metadata.namespace}/{pdb.metadata.name}: disruptionsAllowed=0",
                "현재 드레인/축출이 이 PDB에 의해 차단됨 (의도된 보호일 수 있음)",
                namespace=pdb.metadata.namespace, resource=pdb.metadata.name,
            )


def _check_events(ctx: ClusterContext, result: CheckResult) -> None:
    events = safe_call(result, "list events", ctx.core.list_event_for_all_namespaces)
    if not events:
        return
    counter: Counter = Counter()
    sample_evidence: dict[str, str] = {}
    for ev in events.items:
        if ev.type != "Warning":
            continue
        obj = ev.involved_object
        key = (ev.metadata.namespace, ev.reason, obj.name if obj else "")
        counter[key] += 1
        sample_evidence[key] = ev.message or ""

    for (namespace, reason, obj_name), count in counter.items():
        if count < 3:
            continue
        result.add(
            "repeated_warning_event", Severity.WARNING,
            f"반복 이벤트 {reason} x{count} ({namespace}/{obj_name})",
            sample_evidence.get((namespace, reason, obj_name), ""),
            namespace=namespace, resource=obj_name,
            reproduce_cmd=f"kubectl get events -n {namespace} --field-selector reason={reason}",
        )


def _check_resource_usage(ctx: ClusterContext, result: CheckResult) -> None:
    node_metrics = safe_call(
        result, "list node metrics (metrics-server)",
        ctx.custom.list_cluster_custom_object, "metrics.k8s.io", "v1beta1", "nodes",
    )
    if node_metrics is None:
        result.degrade("metrics-server 미가용 — observability 도메인의 Prometheus 지표로 대체 확인 권장")
