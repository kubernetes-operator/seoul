from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_change_password_roundtrip():
    """Changes the password, confirms old creds stop working and new ones
    work, then changes it back so later tests (which share the same SQLite
    file for the whole pytest session) aren't affected by execution order.
    """
    assert client.get("/api/runs", auth=("testuser", "testpass")).status_code == 200

    try:
        resp = client.put(
            "/api/auth/password",
            auth=("testuser", "testpass"),
            json={"current_password": "testpass", "new_username": None, "new_password": "newpassword123"},
        )
        assert resp.status_code == 200

        assert client.get("/api/runs", auth=("testuser", "testpass")).status_code == 401
        assert client.get("/api/runs", auth=("testuser", "newpassword123")).status_code == 200
    finally:
        client.put(
            "/api/auth/password",
            auth=("testuser", "newpassword123"),
            json={"current_password": "newpassword123", "new_username": None, "new_password": "testpass"},
        )

    assert client.get("/api/runs", auth=("testuser", "testpass")).status_code == 200


def test_change_password_wrong_current_rejected_and_nothing_changes():
    resp = client.put(
        "/api/auth/password",
        auth=("testuser", "testpass"),
        json={"current_password": "wrongcurrent", "new_username": None, "new_password": "irrelevant123"},
    )
    assert resp.status_code == 401
    assert client.get("/api/runs", auth=("testuser", "testpass")).status_code == 200


def test_change_password_requires_auth_itself():
    resp = client.put(
        "/api/auth/password",
        json={"current_password": "testpass", "new_username": None, "new_password": "irrelevant123"},
    )
    assert resp.status_code == 401


def test_change_password_too_short_rejected():
    resp = client.put(
        "/api/auth/password",
        auth=("testuser", "testpass"),
        json={"current_password": "testpass", "new_username": None, "new_password": "short"},
    )
    assert resp.status_code == 422
    assert client.get("/api/runs", auth=("testuser", "testpass")).status_code == 200


def test_change_username_too():
    try:
        resp = client.put(
            "/api/auth/password",
            auth=("testuser", "testpass"),
            json={"current_password": "testpass", "new_username": "newadmin", "new_password": "temporarypass1"},
        )
        assert resp.status_code == 200
        assert client.get("/api/runs", auth=("newadmin", "temporarypass1")).status_code == 200
        assert client.get("/api/runs", auth=("testuser", "temporarypass1")).status_code == 401
    finally:
        client.put(
            "/api/auth/password",
            auth=("newadmin", "temporarypass1"),
            json={"current_password": "temporarypass1", "new_username": "testuser", "new_password": "testpass"},
        )
