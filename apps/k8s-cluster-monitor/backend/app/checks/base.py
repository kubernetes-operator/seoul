"""Shared types for all check domains.

A check module's `run(ctx)` function returns a CheckResult. Findings are
fingerprinted so the API layer can upsert them across runs and compute
first-seen/last-seen/resolved-since-last-run without each check module
having to know anything about persistence.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class Severity(str, Enum):
    CRITICAL = "critical"
    WARNING = "warning"
    INFO = "info"


@dataclass
class Finding:
    domain: str
    check_id: str
    severity: Severity
    title: str
    detail: str
    namespace: Optional[str] = None
    resource: Optional[str] = None
    evidence: Optional[str] = None
    reproduce_cmd: Optional[str] = None

    @property
    def fingerprint(self) -> str:
        # Identity is (domain, check_id, namespace, resource) — NOT detail/evidence,
        # so the same underlying issue keeps the same fingerprint across runs even
        # if wording or counts drift slightly, which is what makes first-seen/
        # last-seen tracking meaningful instead of creating a "new" finding every run.
        key = f"{self.domain}|{self.check_id}|{self.namespace or ''}|{self.resource or ''}"
        return hashlib.sha256(key.encode()).hexdigest()[:16]


class CheckResult:
    """Accumulates findings for one domain's run and tracks degraded/error status.

    A domain is "degraded" (not "error") when a sub-check couldn't complete
    (e.g. a CRD isn't installed, an in-cluster HTTP call timed out) but other
    sub-checks in the same domain still produced valid results. This mirrors
    the harness principle from the original SKILL.md files: a missing signal
    should never be silently reported as "all clear".
    """

    def __init__(self, domain: str):
        self.domain = domain
        self.findings: list[Finding] = []
        self.status = "ok"  # ok | degraded | error
        self.status_reasons: list[str] = []

    def add(
        self,
        check_id: str,
        severity: Severity,
        title: str,
        detail: str,
        *,
        namespace: Optional[str] = None,
        resource: Optional[str] = None,
        evidence: Optional[str] = None,
        reproduce_cmd: Optional[str] = None,
    ) -> None:
        self.findings.append(
            Finding(
                domain=self.domain,
                check_id=check_id,
                severity=severity,
                title=title,
                detail=detail,
                namespace=namespace,
                resource=resource,
                evidence=evidence,
                reproduce_cmd=reproduce_cmd,
            )
        )

    def degrade(self, reason: str) -> None:
        if self.status == "ok":
            self.status = "degraded"
        self.status_reasons.append(reason)

    def error(self, reason: str) -> None:
        self.status = "error"
        self.status_reasons.append(reason)

    def summary_counts(self) -> dict:
        counts = {s.value: 0 for s in Severity}
        for f in self.findings:
            counts[f.severity.value] += 1
        return counts


def owner_name(pod) -> str:
    """Collapse a pod to its owning workload name (Deployment's ReplicaSet,
    StatefulSet, or DaemonSet) so per-pod checks produce ONE finding per
    workload instead of one per replica. Falls back to the pod's own name
    for bare pods with no owner. This matters a lot in practice — without
    it, a Deployment with 10 replicas turns one real issue (e.g. "runs as
    root") into 10 near-identical findings.
    """
    owners = getattr(pod.metadata, "owner_references", None) or []
    if owners:
        return owners[0].name
    return pod.metadata.name


def safe_call(result: CheckResult, description: str, fn, *args, **kwargs):
    """Run a k8s/HTTP client call; on 403/404/timeout, degrade the domain
    instead of raising. This is what lets a missing CRD (e.g. no Cilium
    CustomObjects, no Velero installed) or an RBAC gap show up as an honest
    "skipped: <reason>" rather than crashing the whole domain's check run.
    """
    try:
        return fn(*args, **kwargs)
    except Exception as exc:  # noqa: BLE001 - intentionally broad, see docstring
        status_code = getattr(exc, "status", None)
        reason = f"{description}: {exc.__class__.__name__}"
        if status_code == 404:
            reason = f"{description}: not found (CRD/resource likely not installed)"
        elif status_code == 403:
            reason = f"{description}: forbidden (RBAC does not grant this read)"
        result.degrade(reason)
        return None
