def test_healthz_does_not_require_auth(client):
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_runs_endpoint_requires_auth(client):
    # No session cookie → 401.
    resp = client.get("/api/runs")
    assert resp.status_code == 401


def test_runs_endpoint_with_session_succeeds(auth_client):
    resp = auth_client.get("/api/runs")
    assert resp.status_code == 200


def test_login_with_wrong_password_rejected(client):
    resp = client.post("/api/login", json={"username": "admin", "password": "wrongpass"})
    assert resp.status_code == 401
    # Still unauthenticated afterwards.
    assert client.get("/api/runs").status_code == 401


def test_unknown_domain_trigger_returns_404(auth_client):
    resp = auth_client.post("/api/runs/not-a-real-domain")
    assert resp.status_code == 404


def test_api_responses_are_no_store(auth_client, client):
    # Both authed and unauthed /api responses must be uncacheable so no proxy
    # or browser serves a stale body/401 (guards against spurious re-logins).
    assert auth_client.get("/api/runs").headers.get("cache-control") == "no-store"
    assert client.get("/api/runs").headers.get("cache-control") == "no-store"  # 401 too
