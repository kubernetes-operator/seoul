"""Login throttle, session revocation, cross-origin write guard, headers.
Uses a fresh in-memory DB and the seeded default account only."""
import pytest
from fastapi.testclient import TestClient

from app import auth, db, login_guard, models
from app.main import app


@pytest.fixture
def owner(monkeypatch):
    conn = models.get_connection(":memory:")
    monkeypatch.setattr(db, "_connection", conn)
    auth.ensure_seed(conn)
    login_guard.reset()
    c = TestClient(app)
    assert c.post("/api/login", json={"username": "admin", "password": "password"}).status_code == 200
    yield c
    login_guard.reset()
    conn.close()


def test_repeated_failures_lock_the_account(owner):
    c = TestClient(app)
    for _ in range(login_guard.MAX_FAILURES):
        assert c.post("/api/login", json={"username": "admin", "password": "wrong-guess"}).status_code == 401
    locked = c.post("/api/login", json={"username": "admin", "password": "password"})
    assert locked.status_code == 429 and int(locked.headers["retry-after"]) > 0
    login_guard.reset()
    assert c.post("/api/login", json={"username": "admin", "password": "password"}).status_code == 200


def test_password_change_logs_out_other_sessions(owner):
    other = TestClient(app)
    assert other.post("/api/login", json={"username": "admin", "password": "password"}).status_code == 200
    r = owner.put("/api/auth/password", json={"current_password": "password", "new_password": "new-strong-pass"})
    assert r.status_code == 200
    assert owner.get("/api/session").status_code == 200   # caller keeps its session
    assert other.get("/api/session").status_code == 401   # everyone else is out


def test_admin_reset_and_demotion_revoke_target_sessions(owner):
    assert owner.post("/api/ui/users", json={"username": "op2", "password": "op2-pass-123",
                                             "role": "admin", "pages": None}).status_code == 201
    op2 = TestClient(app)
    assert op2.post("/api/login", json={"username": "op2", "password": "op2-pass-123"}).status_code == 200
    assert owner.patch("/api/ui/users/op2", json={"role": "viewer", "pages": ["overview"]}).status_code == 200
    assert op2.get("/api/session").status_code == 401


def test_cross_origin_writes_are_rejected(owner):
    evil = owner.post("/api/runs/not-a-domain", headers={"Origin": "https://lyon.example.test"})
    assert evil.status_code == 403
    same = owner.post("/api/runs/not-a-domain", headers={"Origin": "http://testserver"})
    assert same.status_code == 404  # passed the guard, then normal handling


def test_security_headers_and_secure_cookie(owner):
    r = owner.get("/healthz")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["x-frame-options"] == "DENY"
    assert "frame-ancestors 'none'" in r.headers["content-security-policy"]
    c = TestClient(app)
    login = c.post("/api/login", json={"username": "admin", "password": "password"},
                   headers={"X-Forwarded-Proto": "https"})
    assert "secure" in login.headers["set-cookie"].lower()
    assert "strict-transport-security" in login.headers
