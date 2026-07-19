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

    schedule_cron: str = ""  # empty = disabled; e.g. "0 * * * *" for hourly
    retention_days: int = 30

    basic_auth_username_env: str = "BASIC_AUTH_USERNAME"
    basic_auth_password_env: str = "BASIC_AUTH_PASSWORD"


settings = Settings()
