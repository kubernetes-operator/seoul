import os
import tempfile

import pytest

# Basic Auth must be configured before app.main is imported anywhere, since
# some tests exercise the real FastAPI app via TestClient.
os.environ.setdefault("BASIC_AUTH_USERNAME", "testuser")
os.environ.setdefault("BASIC_AUTH_PASSWORD", "testpass")
os.environ.setdefault("MONITOR_DB_PATH", os.path.join(tempfile.mkdtemp(), "test.db"))

from helpers import FakeClusterContext, ns  # noqa: E402


@pytest.fixture
def fake_ctx():
    return FakeClusterContext()


@pytest.fixture
def make_ns():
    return ns
