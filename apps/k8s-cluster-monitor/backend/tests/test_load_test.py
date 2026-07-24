import asyncio
from unittest.mock import patch

import pytest

from app import load_test
from app.checks.base import Severity
from app.config import settings


class FakeAsyncResponse:
    def __init__(self, status_code=200):
        self.status_code = status_code


class FakeAsyncClient:
    """Stands in for httpx.AsyncClient — every GET succeeds instantly."""

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url):
        return FakeAsyncResponse(200)


class FailingAsyncClient(FakeAsyncClient):
    async def get(self, url):
        return FakeAsyncResponse(500)


@pytest.fixture
def loadtest_enabled(monkeypatch):
    monkeypatch.setattr(settings, "loadtest_enabled", True)


def make_params(**overrides):
    defaults = dict(namespace="demo-app", service="web", port=80, path="/", concurrency=2, duration_seconds=0.3)
    defaults.update(overrides)
    return load_test.LoadTestParams(**defaults)


def test_rejects_namespace_not_on_allowlist(loadtest_enabled):
    with pytest.raises(load_test.LoadTestError) as exc:
        load_test.validate_params(make_params(namespace="kube-system", service="x", port=80))
    assert exc.value.status_code == 400


def test_rejects_port_not_allowlisted_for_service(loadtest_enabled):
    with pytest.raises(load_test.LoadTestError) as exc:
        load_test.validate_params(make_params(namespace="demo-app", service="web", port=9999))
    assert exc.value.status_code == 400


def test_rejects_concurrency_over_cap(loadtest_enabled):
    with pytest.raises(load_test.LoadTestError) as exc:
        load_test.validate_params(make_params(concurrency=settings.loadtest_max_concurrency + 1))
    assert exc.value.status_code == 400


def test_rejects_duration_over_cap(loadtest_enabled):
    with pytest.raises(load_test.LoadTestError) as exc:
        load_test.validate_params(make_params(duration_seconds=settings.loadtest_max_duration_seconds + 1))
    assert exc.value.status_code == 400


def test_rejects_when_feature_disabled(monkeypatch):
    monkeypatch.setattr(settings, "loadtest_enabled", False)
    with pytest.raises(load_test.LoadTestError) as exc:
        load_test.validate_params(make_params())
    assert exc.value.status_code == 503


def test_execute_happy_path(loadtest_enabled):
    with patch("app.load_test.httpx.AsyncClient", FakeAsyncClient):
        result = asyncio.run(load_test.execute(make_params(), triggered_by="tester"))

    assert result["total_requests"] > 0
    assert result["error_count"] == 0
    assert result["triggered_by"] == "tester"
    assert result["namespace"] == "demo-app"
    assert result["p95_ms"] is not None


def test_execute_counts_errors(loadtest_enabled):
    with patch("app.load_test.httpx.AsyncClient", FailingAsyncClient):
        result = asyncio.run(load_test.execute(make_params(), triggered_by="tester"))

    assert result["error_count"] == result["total_requests"]


def test_single_flight_rejects_overlapping_run(loadtest_enabled):
    assert load_test._lock.acquire(blocking=False)
    try:
        with pytest.raises(load_test.LoadTestError) as exc:
            asyncio.run(load_test.execute(make_params(), triggered_by="tester"))
        assert exc.value.status_code == 409
    finally:
        load_test._lock.release()


def test_summary_finding_flags_high_error_rate():
    result = {
        "target": "http://web.demo-app.svc.cluster.local:80/", "namespace": "demo-app",
        "concurrency": 5, "duration_seconds": 10, "total_requests": 100, "error_count": 20,
        "p50_ms": 50, "p95_ms": 90, "p99_ms": 120, "achieved_rps": 10.0, "triggered_by": "tester",
    }
    cr = load_test.build_summary_check_result(result)
    assert any(f.check_id == "load_test_high_error_rate" and f.severity == Severity.WARNING for f in cr.findings)


def test_summary_finding_is_info_when_healthy():
    result = {
        "target": "http://web.demo-app.svc.cluster.local:80/", "namespace": "demo-app",
        "concurrency": 5, "duration_seconds": 10, "total_requests": 100, "error_count": 0,
        "p50_ms": 10, "p95_ms": 20, "p99_ms": 30, "achieved_rps": 10.0, "triggered_by": "tester",
    }
    cr = load_test.build_summary_check_result(result)
    assert len(cr.findings) == 1
    assert cr.findings[0].check_id == "load_test_completed"
    assert cr.findings[0].severity == Severity.INFO


# ---------- API-level (routes_loadtest.py), through the real FastAPI app ----------

def test_targets_endpoint_requires_auth(client):
    assert client.get("/api/loadtest/targets").status_code == 401


def test_targets_endpoint_lists_allowlist(auth_client):
    resp = auth_client.get("/api/loadtest/targets")
    assert resp.status_code == 200
    body = resp.json()
    assert "demo-app" in body["allowed_targets"]


def test_post_loadtest_disabled_by_default_returns_503(auth_client):
    resp = auth_client.post(
        "/api/loadtest",
        json={"namespace": "demo-app", "service": "web", "port": 80, "concurrency": 2, "duration_seconds": 0.2},
    )
    assert resp.status_code == 503


def test_post_loadtest_end_to_end_when_enabled(auth_client, monkeypatch):
    monkeypatch.setattr(settings, "loadtest_enabled", True)
    with patch("app.load_test.httpx.AsyncClient", FakeAsyncClient):
        resp = auth_client.post(
            "/api/loadtest",
            json={"namespace": "demo-app", "service": "web", "port": 80, "concurrency": 2, "duration_seconds": 0.2},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["total_requests"] > 0
    assert body["triggered_by"] == "admin"  # the logged-in session user
    assert "run_id" in body and "load_test_id" in body

    history = auth_client.get("/api/loadtest/history").json()
    assert any(row["id"] == body["load_test_id"] for row in history)


def test_post_loadtest_rejects_disallowed_namespace_via_api(auth_client, monkeypatch):
    monkeypatch.setattr(settings, "loadtest_enabled", True)
    resp = auth_client.post(
        "/api/loadtest",
        json={"namespace": "kube-system", "service": "x", "port": 80, "concurrency": 1, "duration_seconds": 0.2},
    )
    assert resp.status_code == 400
