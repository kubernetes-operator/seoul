from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_healthz_does_not_require_auth():
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_runs_endpoint_requires_auth():
    resp = client.get("/api/runs")
    assert resp.status_code == 401


def test_runs_endpoint_with_correct_auth_succeeds():
    resp = client.get("/api/runs", auth=("testuser", "testpass"))
    assert resp.status_code == 200


def test_runs_endpoint_with_wrong_auth_rejected():
    resp = client.get("/api/runs", auth=("testuser", "wrongpass"))
    assert resp.status_code == 401


def test_unknown_domain_trigger_returns_404():
    resp = client.post("/api/runs/not-a-real-domain", auth=("testuser", "testpass"))
    assert resp.status_code == 404
