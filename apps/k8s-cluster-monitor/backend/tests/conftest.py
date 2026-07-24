import os
import tempfile

import pytest

# The DB path must be set before app.main is imported anywhere, since some
# tests exercise the real FastAPI app via TestClient. Auth is no longer seeded
# from env vars — the app seeds the default admin/password on first DB open
# (see app/auth.py ensure_seed / app/db.py), so tests log in with those.
os.environ.setdefault("MONITOR_DB_PATH", os.path.join(tempfile.mkdtemp(), "test.db"))

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from helpers import FakeClusterContext, ns  # noqa: E402

# Default seeded credentials (see app/auth.py).
ADMIN_USER = "admin"
ADMIN_PASS = "password"


@pytest.fixture
def client():
    """Fresh TestClient with no session cookie (for auth-required checks)."""
    return TestClient(app)


@pytest.fixture
def auth_client():
    """TestClient already logged in as the default admin (cookie persisted)."""
    c = TestClient(app)
    resp = c.post("/api/login", json={"username": ADMIN_USER, "password": ADMIN_PASS})
    assert resp.status_code == 200, resp.text
    return c


@pytest.fixture
def fake_ctx():
    return FakeClusterContext()


@pytest.fixture
def make_ns():
    return ns
