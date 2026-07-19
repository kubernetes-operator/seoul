"""Security audit checks — ported from .claude/skills/k8s-security-audit/SKILL.md.

IMPORTANT invariant: this module must never call any `secrets` API (get/list/
watch). The ServiceAccount's ClusterRole grants no verbs on `secrets` at all
(see deploy/k8s/02-clusterrole.yaml), so any such call would fail — but the
absence is enforced here in code too, as defense in depth, and so a reviewer
never has to wonder "does this module touch secrets?" while reading it.
"""
from __future__ import annotations

from ..k8s_client import ClusterContext
from .base import CheckResult, Severity, owner_name, safe_call

SYSTEM_ROLE_PREFIX = "system:"


def run(ctx: ClusterContext) -> CheckResult:
    result = CheckResult("security")

    _check_rbac(ctx, result)
    _check_pod_security(ctx, result)
    _check_pss_labels(ctx, result)
    _check_serviceaccount_automount(ctx, result)
    _check_certificates(ctx, result)
    _check_image_hygiene(ctx, result)

    result.add(
        "secrets_excluded", Severity.INFO,
        "시크릿 위생 점검은 이 도구에서 제공하지 않음 (설계상 의도됨)",
        "Kubernetes RBAC은 시크릿 데이터를 필드 단위로 숨길 수 없어, get/list 권한을 주는 순간 전체 데이터가 "
        "네트워크로 전달됩니다. 상시 실행되는 웹 도구가 그 권한을 갖는 것 자체가 위험이므로, 이 ServiceAccount에는 "
        "secrets 리소스에 대한 어떤 verb도 부여하지 않았습니다. 시크릿 메타데이터(타입/생성일) 점검이 필요하면 "
        "k8s-security-audit 스킬(Claude Code, 사람이 직접 실행)을 사용하세요.",
    )
    return result


def _check_rbac(ctx: ClusterContext, result: CheckResult) -> None:
    crbs = safe_call(result, "list ClusterRoleBindings", ctx.rbac.list_cluster_role_binding)
    for crb in (crbs.items if crbs else []):
        if crb.role_ref.name != "cluster-admin":
            continue
        for subject in (crb.subjects or []):
            if subject.kind == "Group" and subject.name in ("system:masters",):
                continue
            severity = Severity.CRITICAL if subject.kind == "User" else Severity.WARNING
            result.add(
                "cluster_admin_binding", severity,
                f"cluster-admin 바인딩: {subject.kind} {subject.name}",
                f"ClusterRoleBinding {crb.metadata.name}이 {subject.kind} '{subject.name}'에 cluster-admin 전권 부여",
                resource=crb.metadata.name,
                reproduce_cmd=f"kubectl describe clusterrolebinding {crb.metadata.name}",
            )

    # "cluster-admin" itself is a well-known Kubernetes built-in wildcard role by
    # design — who is BOUND to it is already surfaced above by cluster_admin_binding;
    # flagging the role's existence too would just be redundant noise.
    WILDCARD_EXEMPT = {"cluster-admin"}

    croles = safe_call(result, "list ClusterRoles", ctx.rbac.list_cluster_role)
    for role in (croles.items if croles else []):
        if role.metadata.name.startswith(SYSTEM_ROLE_PREFIX) or role.metadata.name in WILDCARD_EXEMPT:
            continue
        for rule in (role.rules or []):
            if "*" in (rule.resources or []) and "*" in (rule.verbs or []):
                result.add(
                    "wildcard_clusterrole", Severity.WARNING,
                    f"와일드카드 권한 ClusterRole: {role.metadata.name}",
                    "resources=* and verbs=* — 바인딩 대상 확인 권장", resource=role.metadata.name,
                    reproduce_cmd=f"kubectl describe clusterrole {role.metadata.name}",
                )
                break


def _check_pod_security(ctx: ClusterContext, result: CheckResult) -> None:
    system_namespaces = {"kube-system", "cilium-secrets", "gpu-operator", "metallb-system"}
    # Findings are per WORKLOAD (Deployment/StatefulSet/DaemonSet), not per pod
    # replica — otherwise a Deployment with N replicas produces N near-identical
    # findings for the same underlying config issue. `seen` dedupes within this run.
    seen: set[tuple[str, str, str]] = set()

    for pod in ctx.pods:
        ns = pod.metadata.namespace
        spec = pod.spec
        owner = owner_name(pod)
        host_ns = bool(spec.host_network or spec.host_pid or spec.host_ipc)
        privileged = any(
            (c.security_context and c.security_context.privileged) for c in (spec.containers or [])
        )

        if host_ns and ("host_namespace_pod", ns, owner) not in seen:
            seen.add(("host_namespace_pod", ns, owner))
            severity = Severity.INFO if ns in system_namespaces else Severity.WARNING
            result.add(
                "host_namespace_pod", severity,
                f"{ns}/{owner}: hostNetwork/hostPID/hostIPC 사용",
                "호스트 네임스페이스 공유 — 노드 전체 접근 경로가 될 수 있음",
                namespace=ns, resource=owner,
            )
        if privileged and ("privileged_container", ns, owner) not in seen:
            seen.add(("privileged_container", ns, owner))
            severity = Severity.INFO if ns in system_namespaces else Severity.WARNING
            result.add(
                "privileged_container", severity,
                f"{ns}/{owner}: privileged 컨테이너",
                "특권 컨테이너 — 시스템 애드온이면 정상일 수 있으나 사용자 네임스페이스면 검토 필요",
                namespace=ns, resource=owner,
            )
        run_as_nonroot = spec.security_context and spec.security_context.run_as_non_root
        if not run_as_nonroot and ns not in system_namespaces and ("run_as_root_allowed", ns, owner) not in seen:
            per_container_set = all(
                c.security_context and c.security_context.run_as_non_root for c in (spec.containers or [])
            )
            if not per_container_set:
                seen.add(("run_as_root_allowed", ns, owner))
                result.add(
                    "run_as_root_allowed", Severity.INFO,
                    f"{ns}/{owner}: runAsNonRoot 미설정",
                    "컨테이너가 root(uid 0)로 실행될 수 있음", namespace=ns, resource=owner,
                )


def _check_pss_labels(ctx: ClusterContext, result: CheckResult) -> None:
    for ns in ctx.namespaces:
        labels = ns.metadata.labels or {}
        enforce = labels.get("pod-security.kubernetes.io/enforce", "none")
        if enforce == "none" and not ns.metadata.name.startswith("kube-"):
            result.add(
                "pss_not_enforced", Severity.INFO,
                f"네임스페이스 {ns.metadata.name}: Pod Security Standards 미적용",
                "enforce 라벨 없음 — 최소 warn=baseline부터 단계 도입 권장", namespace=ns.metadata.name,
            )


def _check_serviceaccount_automount(ctx: ClusterContext, result: CheckResult) -> None:
    sas = safe_call(result, "list ServiceAccounts", ctx.core.list_service_account_for_all_namespaces)
    for sa in (sas.items if sas else []):
        if sa.automount_service_account_token is not False:
            result.add(
                "sa_automount_not_disabled", Severity.INFO,
                f"ServiceAccount {sa.metadata.namespace}/{sa.metadata.name}: automount 미차단",
                "이 SA를 사용하는 파드에 API 토큰이 자동 마운트됨 (파드 스펙에서 재정의됐을 수 있음)",
                namespace=sa.metadata.namespace, resource=sa.metadata.name,
            )


def _check_certificates(ctx: ClusterContext, result: CheckResult) -> None:
    import datetime

    certs = safe_call(result, "list Certificates", ctx.list_cluster_custom, "cert-manager.io", "v1", "certificates")
    for cert in (certs or []):
        not_after = cert.get("status", {}).get("notAfter")
        if not not_after:
            continue
        try:
            expiry = datetime.datetime.fromisoformat(not_after.replace("Z", "+00:00"))
        except ValueError:
            continue
        days_left = (expiry - datetime.datetime.now(datetime.timezone.utc)).days
        meta = cert.get("metadata", {})
        if days_left <= 30:
            result.add(
                "certificate_expiring", Severity.CRITICAL,
                f"인증서 {meta.get('namespace')}/{meta.get('name')}: {days_left}일 후 만료",
                "긴급 — 갱신 확인 필요", namespace=meta.get("namespace"), resource=meta.get("name"),
            )
        elif days_left <= 90:
            result.add(
                "certificate_expiring", Severity.WARNING,
                f"인증서 {meta.get('namespace')}/{meta.get('name')}: {days_left}일 후 만료",
                "경고 구간 — 자동 갱신 동작 확인 권장", namespace=meta.get("namespace"), resource=meta.get("name"),
            )


def _check_image_hygiene(ctx: ClusterContext, result: CheckResult) -> None:
    seen = set()
    for pod in ctx.pods:
        for c in (pod.spec.containers or []) + (pod.spec.init_containers or []):
            image = c.image
            if image in seen:
                continue
            seen.add(image)
            is_latest = image.endswith(":latest") or ":" not in image.split("/")[-1]
            if is_latest:
                result.add(
                    "image_latest_tag", Severity.INFO,
                    f"{pod.metadata.namespace}/{owner_name(pod)}: 이미지 태그 미고정 ({image})",
                    "latest 또는 태그 미지정 — 재배포 시 예측 불가한 이미지 변동 가능",
                    namespace=pod.metadata.namespace, resource=owner_name(pod),
                )
