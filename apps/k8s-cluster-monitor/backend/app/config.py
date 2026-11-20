"""Environment-driven configuration. No secrets live here — the Basic Auth
credential is read directly from mounted Secret env vars in auth.py, not
duplicated into this settings object.
"""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MONITOR_")

    db_path: str = "/data/monitor.db"
    # Absolute path used inside the container; main.py falls back to a
    # repo-relative path automatically when this doesn't exist (local dev).
    static_dir: str = "/app/frontend/static"

    prometheus_url: str = "http://prometheus-stack-kube-prom-prometheus.monitoring.svc.cluster.local:9090"
    alertmanager_url: str = "http://prometheus-stack-kube-prom-alertmanager.monitoring.svc.cluster.local:9093"
    # Port 80, not Grafana's container port 3000 — kube-prometheus-stack's
    # Service fronts it on 80 (confirmed against the real cluster; see
    # deploy/README.md changelog note).
    grafana_url: str = "http://prometheus-stack-grafana.monitoring.svc.cluster.local:80"
    loki_url: str = "http://loki-stack.logging.svc.cluster.local:3100"
    http_timeout_seconds: float = 5.0

    # Domains & pages (checks/routes.py): probes go to the Gateway data plane
    # Service in-cluster, with the route hostname as Host header + TLS SNI.
    # Keep in sync with deploy/k8s/04-networkpolicy.yaml's nginx-gateway egress.
    routes_gateway_host: str = "nginx-gateway.nginx-gateway.svc.cluster.local"
    routes_gateway_https_port: int = 443
    routes_probe_timeout_seconds: float = 5.0
    routes_latency_warning_ms: float = 2000.0
    routes_cert_warning_days: int = 14

    schedule_cron: str = ""  # empty = disabled; e.g. "0 * * * *" for hourly
    retention_days: int = 30

    basic_auth_username_env: str = "BASIC_AUTH_USERNAME"
    basic_auth_password_env: str = "BASIC_AUTH_PASSWORD"

    # Load-test feature — disabled by default even after this ships; see
    # deploy/README.md. Namespace allow-list must be kept in sync with the
    # CiliumNetworkPolicy egress rules in deploy/k8s/04-networkpolicy.yaml —
    # each has a comment cross-referencing the other.
    loadtest_enabled: bool = False
    loadtest_allowed_namespaces: str = "demo-app,gitea,model-factory,os-monitor"
    loadtest_max_duration_seconds: int = 60
    loadtest_max_concurrency: int = 50
    loadtest_max_total_requests: int = 5000

    @property
    def loadtest_allowed_namespaces_list(self) -> list[str]:
        return [n.strip() for n in self.loadtest_allowed_namespaces.split(",") if n.strip()]


settings = Settings()
