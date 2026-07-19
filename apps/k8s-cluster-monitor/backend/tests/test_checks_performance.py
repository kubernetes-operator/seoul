from unittest.mock import patch

from app.checks import performance
from app.checks.base import Severity


class FakeResponse:
    def __init__(self, json_data):
        self._json = json_data

    def raise_for_status(self):
        pass

    def json(self):
        return self._json


def _series(value, **labels):
    return {"metric": labels, "value": [0, str(value)]}


def test_cpu_throttling_flagged_above_threshold():
    def fake_get(url, params=None, timeout=None):
        query = params["query"]
        if "cfs_throttled" in query:
            return FakeResponse({"data": {"result": [_series(0.5, namespace="demo-app", pod="api-1", container="api")]}})
        return FakeResponse({"data": {"result": []}})

    with patch("app.checks.performance.httpx.get", side_effect=fake_get):
        result = performance.run(None)

    matches = [f for f in result.findings if f.check_id == "cpu_throttling_high"]
    assert len(matches) == 1
    assert matches[0].severity == Severity.WARNING
    assert matches[0].namespace == "demo-app"


def test_node_saturation_flagged():
    def fake_get(url, params=None, timeout=None):
        query = params["query"]
        if "node_cpu_seconds_total" in query:
            return FakeResponse({"data": {"result": [_series(0.95, instance="node-1")]}})
        return FakeResponse({"data": {"result": []}})

    with patch("app.checks.performance.httpx.get", side_effect=fake_get):
        result = performance.run(None)

    assert any(f.check_id == "node_cpu_saturated" for f in result.findings)


def test_no_app_metrics_produces_no_findings_and_no_coverage_gap():
    def fake_get(url, params=None, timeout=None):
        return FakeResponse({"data": {"result": []}})

    with patch("app.checks.performance.httpx.get", side_effect=fake_get):
        result = performance.run(None)

    assert result.findings == []
    assert result.status == "ok"


def test_query_failure_degrades_not_crashes():
    def fake_get(url, params=None, timeout=None):
        raise ConnectionError("no route to prometheus")

    with patch("app.checks.performance.httpx.get", side_effect=fake_get):
        result = performance.run(None)

    assert result.status == "degraded"
    assert result.findings == []


def test_app_latency_and_error_rate_flagged_when_instrumented():
    def fake_get(url, params=None, timeout=None):
        query = params["query"]
        if "count(http_request_duration_seconds_bucket)" in query:
            return FakeResponse({"data": {"result": [_series(1, namespace="demo-app")]}})
        if "histogram_quantile" in query:
            return FakeResponse({"data": {"result": [_series(1.5)]}})
        if "http_requests_total" in query:
            return FakeResponse({"data": {"result": [_series(0.10)]}})
        return FakeResponse({"data": {"result": []}})

    with patch("app.checks.performance.httpx.get", side_effect=fake_get):
        result = performance.run(None)

    assert any(f.check_id == "app_p95_latency_high" for f in result.findings)
    assert any(f.check_id == "app_error_rate_high" for f in result.findings)
