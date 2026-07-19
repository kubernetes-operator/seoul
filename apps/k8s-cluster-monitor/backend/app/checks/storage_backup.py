"""Storage & Velero backup checks — ported from
.claude/skills/k8s-storage-backup-check/SKILL.md.

Read-only: get/list only, plus a namespaced Role in `velero` granting
`pods/log` (see deploy/k8s/03b-role-velero-logs.yaml) for the log-scan
sub-check. No restore/backup-create calls are ever made — this module
only ever proposes a restore drill in the finding text, exactly like the
manual skill did, and never a button/API to execute one.
"""
from __future__ import annotations

import datetime
import re

from ..k8s_client import ClusterContext
from .base import CheckResult, Severity, safe_call

ERROR_PATTERN = re.compile(r"error", re.IGNORECASE)


def run(ctx: ClusterContext) -> CheckResult:
    result = CheckResult("storage_backup")

    _check_pv_pvc(ctx, result)
    _check_velero_backups(ctx, result)
    _check_velero_logs(ctx, result)

    return result


def _check_pv_pvc(ctx: ClusterContext, result: CheckResult) -> None:
    pvs = safe_call(result, "list PVs", ctx.core.list_persistent_volume)
    for pv in (pvs.items if pvs else []):
        if pv.status.phase == "Released":
            result.add(
                "pv_released_orphan", Severity.WARNING,
                f"PV {pv.metadata.name}: Released 상태로 잔류",
                f"reclaimPolicy={pv.spec.persistent_volume_reclaim_policy} — 원 클레임 삭제 후 정리되지 않음",
                resource=pv.metadata.name,
                reproduce_cmd=f"kubectl describe pv {pv.metadata.name}",
            )
        elif pv.status.phase == "Failed":
            result.add(
                "pv_failed", Severity.CRITICAL, f"PV {pv.metadata.name}: Failed",
                "PV가 Failed 상태", resource=pv.metadata.name,
            )

    pvcs = safe_call(result, "list PVCs", ctx.core.list_persistent_volume_claim_for_all_namespaces)
    for pvc in (pvcs.items if pvcs else []):
        if pvc.status.phase != "Bound":
            result.add(
                "pvc_not_bound", Severity.WARNING,
                f"PVC {pvc.metadata.namespace}/{pvc.metadata.name}: {pvc.status.phase}",
                "바인딩되지 않음 — StorageClass 불일치 또는 프로비저너 오류 가능",
                namespace=pvc.metadata.namespace, resource=pvc.metadata.name,
                reproduce_cmd=f"kubectl -n {pvc.metadata.namespace} describe pvc {pvc.metadata.name}",
            )


def _check_velero_backups(ctx: ClusterContext, result: CheckResult) -> None:
    backups = safe_call(result, "list Velero backups", ctx.list_namespaced_custom,
                         "velero.io", "v1", "velero", "backups")
    if backups is None:
        return

    completed_times = []
    failed_count = 0
    for b in backups:
        phase = b.get("status", {}).get("phase", "Unknown")
        meta = b.get("metadata", {})
        if phase == "Completed":
            completion = b.get("status", {}).get("completionTimestamp")
            if completion:
                try:
                    completed_times.append(datetime.datetime.fromisoformat(completion.replace("Z", "+00:00")))
                except ValueError:
                    pass
        elif phase in ("FailedValidation", "PartiallyFailed", "Failed"):
            failed_count += 1
            result.add(
                "velero_backup_failed", Severity.CRITICAL,
                f"Velero 백업 실패: {meta.get('name')} ({phase})",
                b.get("status", {}).get("validationErrors", [""])[0] if b.get("status", {}).get("validationErrors") else "",
                namespace="velero", resource=meta.get("name"),
                reproduce_cmd=f"kubectl -n velero describe backup {meta.get('name')}",
            )

    if not completed_times:
        result.add(
            "velero_no_successful_backup", Severity.CRITICAL,
            "유효한(Completed) Velero 백업이 하나도 없음",
            f"보존 창 내 백업 {len(backups)}건 중 성공 0건 — RPO 사실상 무한, 재해복구 안전망 없음. "
            "복원력/카오스 테스트는 이 문제 해결 전까지 보류 권고.",
        )
    else:
        latest = max(completed_times)
        age_hours = (datetime.datetime.now(datetime.timezone.utc) - latest).total_seconds() / 3600
        if age_hours > 48:
            result.add(
                "velero_backup_stale", Severity.WARNING,
                f"최근 성공 백업이 {age_hours:.0f}시간 전",
                "예상 백업 주기보다 오래 성공한 백업이 없음 — RPO 저하",
            )

    vsls = safe_call(result, "list VolumeSnapshotLocations", ctx.list_namespaced_custom,
                      "velero.io", "v1", "velero", "volumesnapshotlocations")
    if vsls and len(vsls) > 1:
        result.add(
            "velero_multiple_vsl", Severity.INFO,
            f"VolumeSnapshotLocation {len(vsls)}개 존재, 기본값 미지정 시 검증 실패 위험",
            "스케줄/백업 spec에 volumeSnapshotLocations 명시 또는 서버 기본값 지정 필요",
            namespace="velero",
        )

    bsls = safe_call(result, "list BackupStorageLocations", ctx.list_namespaced_custom,
                      "velero.io", "v1", "velero", "backupstoragelocations")
    for bsl in (bsls or []):
        phase = bsl.get("status", {}).get("phase")
        if phase != "Available":
            meta = bsl.get("metadata", {})
            result.add(
                "velero_bsl_unavailable", Severity.CRITICAL,
                f"BackupStorageLocation {meta.get('name')}: {phase}",
                "백업 저장소(MinIO 등) 연결 문제 가능", namespace="velero", resource=meta.get("name"),
            )


def _check_velero_logs(ctx: ClusterContext, result: CheckResult) -> None:
    velero_pods = [p for p in ctx.pods if p.metadata.namespace == "velero"
                   and p.metadata.name.startswith("velero-") and not p.metadata.name.startswith("velero-node-agent")]
    for pod in velero_pods[:3]:  # bounded — this is a spot-check, not exhaustive log analysis
        logs = safe_call(
            result, f"read logs {pod.metadata.name}",
            ctx.core.read_namespaced_pod_log, pod.metadata.name, "velero", tail_lines=200,
        )
        if logs is None:
            continue
        error_lines = [line for line in logs.splitlines() if ERROR_PATTERN.search(line)]
        if error_lines:
            result.add(
                "velero_log_errors", Severity.WARNING,
                f"velero 파드 {pod.metadata.name} 로그에 error 라인 {len(error_lines)}건",
                error_lines[0][:300], namespace="velero", resource=pod.metadata.name,
                reproduce_cmd=f"kubectl -n velero logs {pod.metadata.name} --tail=200 | grep -i error",
            )
