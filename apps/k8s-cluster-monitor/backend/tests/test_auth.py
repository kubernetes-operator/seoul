"""Form-login + multi-user auth. All tests share one SQLite file for the
pytest session, so anything that mutates users (add/change) uses a unique
name or reverts, to stay order-independent.
"""
from fastapi.testclient import TestClient

from app.main import app


def test_session_lifecycle(auth_client):
    # Logged in → /api/session returns who we are, protected routes work.
    me = auth_client.get("/api/session")
    assert me.status_code == 200
    assert me.json()["username"] == "admin"
    assert auth_client.get("/api/runs").status_code == 200

    # Logout clears the session; protected routes 401 again.
    assert auth_client.post("/api/logout").status_code == 200
    assert auth_client.get("/api/session").status_code == 401
    assert auth_client.get("/api/runs").status_code == 401


def test_protected_route_requires_session(client):
    assert client.get("/api/runs").status_code == 401
    assert client.get("/api/session").status_code == 401


def test_add_user_then_login(auth_client):
    resp = auth_client.post("/api/users", json={"username": "alice", "password": "alicepass1"})
    assert resp.status_code == 201

    # A brand-new client can log in as the added user.
    other = TestClient(app)
    assert other.post("/api/login", json={"username": "alice", "password": "alicepass1"}).status_code == 200
    assert other.get("/api/runs").status_code == 200

    # Listing shows both admin and the new user.
    users = auth_client.get("/api/users").json()["users"]
    assert "admin" in users and "alice" in users


def test_add_duplicate_user_conflicts(auth_client):
    auth_client.post("/api/users", json={"username": "bob", "password": "bobpass12"})
    dup = auth_client.post("/api/users", json={"username": "bob", "password": "bobpass12"})
    assert dup.status_code == 409


def test_add_user_short_password_rejected(auth_client):
    resp = auth_client.post("/api/users", json={"username": "shorty", "password": "short"})
    assert resp.status_code == 422  # pydantic min_length


def test_add_user_requires_auth(client):
    assert client.post("/api/users", json={"username": "x", "password": "longenough1"}).status_code == 401


def test_change_own_password_roundtrip(auth_client):
    """Change admin's password, confirm a fresh login needs the new one, then
    revert so the shared admin/password default is intact for other tests."""
    try:
        resp = auth_client.put(
            "/api/auth/password",
            json={"current_password": "password", "new_password": "newadminpass1"},
        )
        assert resp.status_code == 200
        # Existing session stays valid (token unchanged).
        assert auth_client.get("/api/runs").status_code == 200
        # Fresh login requires the new password.
        fresh = TestClient(app)
        assert fresh.post("/api/login", json={"username": "admin", "password": "password"}).status_code == 401
        assert fresh.post("/api/login", json={"username": "admin", "password": "newadminpass1"}).status_code == 200
    finally:
        auth_client.put(
            "/api/auth/password",
            json={"current_password": "newadminpass1", "new_password": "password"},
        )
    assert TestClient(app).post("/api/login", json={"username": "admin", "password": "password"}).status_code == 200


def test_change_password_wrong_current_rejected(auth_client):
    resp = auth_client.put(
        "/api/auth/password",
        json={"current_password": "notmypassword", "new_password": "irrelevant123"},
    )
    assert resp.status_code == 401
    # Default still works.
    assert TestClient(app).post("/api/login", json={"username": "admin", "password": "password"}).status_code == 200
