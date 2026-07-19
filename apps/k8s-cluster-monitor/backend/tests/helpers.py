"""Test-only helpers: a duck-typed fake ClusterContext (no real kubeconfig or
network access required) and a terse recursive namespace builder for
constructing fake Kubernetes API objects that match the attribute access
patterns used in app/checks/*.py (e.g. node.status.conditions[0].type).
"""
from __future__ import annotations

import types
from unittest.mock import MagicMock


def ns(**kwargs):
    def convert(v):
        if isinstance(v, dict):
            return ns(**v)
        if isinstance(v, list):
            return [convert(i) for i in v]
        return v

    return types.SimpleNamespace(**{k: convert(v) for k, v in kwargs.items()})


class FakeClusterContext:
    """Matches the attribute surface of app.k8s_client.ClusterContext without
    calling load_config() or touching a real cluster."""

    def __init__(self):
        self.core = MagicMock()
        self.apps = MagicMock()
        self.rbac = MagicMock()
        self.policy = MagicMock()
        self.autoscaling = MagicMock()
        self.networking = MagicMock()
        self.storage = MagicMock()
        self.custom = MagicMock()

        self.pods = []
        self.namespaces = []

        # Sensible empty defaults so a test only needs to override what it cares about.
        self.core.list_node.return_value = ns(items=[])
        self.core.list_event_for_all_namespaces.return_value = ns(items=[])
        self.core.list_service_account_for_all_namespaces.return_value = ns(items=[])
        self.core.list_service_for_all_namespaces.return_value = ns(items=[])
        self.core.list_endpoints_for_all_namespaces.return_value = ns(items=[])
        self.core.list_persistent_volume.return_value = ns(items=[])
        self.core.list_persistent_volume_claim_for_all_namespaces.return_value = ns(items=[])
        self.apps.list_deployment_for_all_namespaces.return_value = ns(items=[])
        self.apps.list_stateful_set_for_all_namespaces.return_value = ns(items=[])
        self.apps.list_daemon_set_for_all_namespaces.return_value = ns(items=[])
        self.autoscaling.list_horizontal_pod_autoscaler_for_all_namespaces.return_value = ns(items=[])
        self.policy.list_pod_disruption_budget_for_all_namespaces.return_value = ns(items=[])
        self.rbac.list_cluster_role_binding.return_value = ns(items=[])
        self.rbac.list_cluster_role.return_value = ns(items=[])
        self.networking.list_network_policy_for_all_namespaces.return_value = ns(items=[])
        self.custom.list_cluster_custom_object.return_value = {"items": []}
        self.custom.list_namespaced_custom_object.return_value = {"items": []}

    def list_cluster_custom(self, group, version, plural):
        return self.custom.list_cluster_custom_object(group, version, plural).get("items", [])

    def list_namespaced_custom(self, group, version, namespace, plural):
        return self.custom.list_namespaced_custom_object(group, version, namespace, plural).get("items", [])
