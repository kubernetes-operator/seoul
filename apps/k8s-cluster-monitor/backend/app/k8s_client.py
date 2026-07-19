"""Kubernetes API access, shared across all check modules.

Loads in-cluster config when running as a pod (the normal path in
production), falling back to the local kubeconfig for `uvicorn app.main:app
--reload` development against a real cluster. Pods and namespaces are
fetched once per ClusterContext (i.e. once per check run) and cached, since
health/network/security all want the same lists and there's no reason to
hit the API server 5x per run for identical data.
"""
from __future__ import annotations

from kubernetes import client, config as k8s_config


def load_config() -> None:
    try:
        k8s_config.load_incluster_config()
    except k8s_config.ConfigException:
        k8s_config.load_kube_config()


class ClusterContext:
    def __init__(self):
        load_config()
        self.core = client.CoreV1Api()
        self.apps = client.AppsV1Api()
        self.rbac = client.RbacAuthorizationV1Api()
        self.policy = client.PolicyV1Api()
        self.autoscaling = client.AutoscalingV1Api()
        self.networking = client.NetworkingV1Api()
        self.storage = client.StorageV1Api()
        self.custom = client.CustomObjectsApi()

        self._pods = None
        self._namespaces = None

    @property
    def pods(self):
        if self._pods is None:
            self._pods = self.core.list_pod_for_all_namespaces().items
        return self._pods

    @property
    def namespaces(self):
        if self._namespaces is None:
            self._namespaces = self.core.list_namespace().items
        return self._namespaces

    def list_cluster_custom(self, group: str, version: str, plural: str):
        return self.custom.list_cluster_custom_object(group, version, plural).get("items", [])

    def list_namespaced_custom(self, group: str, version: str, namespace: str, plural: str):
        return self.custom.list_namespaced_custom_object(group, version, namespace, plural).get("items", [])
