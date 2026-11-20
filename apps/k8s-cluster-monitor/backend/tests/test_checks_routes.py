"""Domains & pages (checks/routes.py): discovery, classification, findings,
persistence and page access. Probes are injected — no network access."""
import time

from fastapi.testclient import TestClient

from app import auth, db, models
from app.checks import routes
from app.checks.routes import HttpInfo, TlsInfo
from app.main import app


def _route(ns, name, hosts, matches, created="2026-11-18T08:52:07Z", accepted="True"):
    return {
        "metadata": {"namespace": ns, "name": name, "creationTimestamp": created},
        "spec": {"hostnames": hosts, "rules": [{"matches": matches}]},
        "status": {"parents": [{"conditions": [{"type": "Accepted", "status": accepted}]}]},
    }


ROUTES = [
    _route("shop", "shop-route", ["shop.example.test"], [
        {"path": {"type": "PathPrefix", "value": "/"}},
        {"path": {"type": "PathPrefix", "value": "/api/auth"}},
        {"path": {"type": "PathPrefix", "value": "/api/down"}},
        {"path": {"type": "Exact", "value": "/"}},  # duplicate of "/" -> deduped
        {"path": {"type": "RegularExpression", "value": "/v[0-9]+"}},
    ]),
    _route("blog", "blog-route", ["blog.example.test"], [{"path": {"type": "PathPrefix", "value": "/"}}],
           accepted="False"),
    _route("misc", "wild", ["*.example.test"], []),
]

HTTP = {
    ("shop.example.test", "/"): HttpInfo(200, 40.0),
    ("shop.example.test", "/api/auth"): HttpInfo(401, 30.0),
    ("shop.example.test", "/api/down"): HttpInfo(503, 20.0),
    ("blog.example.test", "/"): HttpInfo(None, None, "ConnectError"),
}
TLS = {
    "shop.example.test": TlsInfo(True, time.time() + 3 * 86400),
    "blog.example.test": TlsInfo(False, None, "Hostname mismatch"),
}


def _run(fake_ctx):
    fake_ctx.custom.list_cluster_custom_object.return_value = {"items": ROUTES}
    return routes.run(fake_ctx, http_probe=lambda h, p: HTTP[(h, p)], tls_probe=lambda h: TLS[h])


def test_discovery_dedupes_and_skips_unprobeable(fake_ctx):
    result = _run(fake_ctx)
    by_key = {(p["host"], p["path"]): p for p in result.probes}
    assert len([k for k in by_key if k == ("shop.example.test", "/")]) == 1
    assert by_key[("shop.example.test", "/v[0-9]+")]["state"] == "skipped"
    assert by_key[("*.example.test", "/")]["state"] == "skipped"
    assert by_key[("shop.example.test", "/")]["state"] == "ok"
    assert by_key[("shop.example.test", "/api/auth")]["state"] == "auth"  # login wall = reachable
    assert by_key[("shop.example.test", "/api/down")]["state"] == "server_error"
    assert by_key[("blog.example.test", "/")]["state"] == "unreachable"


def test_findings_cover_failures_tls_and_acceptance(fake_ctx):
    checks = {(f.check_id, f.resource, f.severity.value) for f in _run(fake_ctx).findings}
    assert ("route_server_error", "shop.example.test/api/down", "critical") in checks
    assert ("route_unreachable", "blog.example.test/", "critical") in checks
    assert ("route_cert_expiring", "shop.example.test", "critical") in checks  # 3 days left
    assert ("route_tls_invalid", "blog.example.test", "warning") in checks
    assert ("route_not_accepted", "blog-route", "warning") in checks
    # A 401 is not a problem, and nothing is reported for skipped rows.
    assert not any(r and r.startswith("shop.example.test/api/auth") for _, r, _ in checks)


def test_missing_crd_degrades_instead_of_crashing(fake_ctx):
    err = Exception("nope")
    err.status = 404
    fake_ctx.custom.list_cluster_custom_object.side_effect = err
    result = routes.run(fake_ctx, http_probe=lambda h, p: None, tls_probe=lambda h: None)
    assert result.status == "degraded" and result.probes == []


def test_probes_persist_with_history_and_page_access(fake_ctx, monkeypatch):
    conn = models.get_connection(":memory:")
    monkeypatch.setattr(db, "_connection", conn)
    auth.ensure_seed(conn)
    for i in range(2):
        result = _run(fake_ctx)
        run_id = models.save_run(conn, result, 1000.0 + i, 1001.0 + i)
        models.save_route_probes(conn, run_id, 1000.0 + i, result.probes)

    owner = TestClient(app)
    assert owner.post("/api/login", json={"username": auth.DEFAULT_USERNAME,
                                          "password": auth.DEFAULT_PASSWORD}).status_code == 200
    data = owner.get("/api/routes/latest").json()
    row = next(p for p in data["probes"] if p["host"] == "shop.example.test" and p["path"] == "/")
    assert [h["state"] for h in row["history"]] == ["ok", "ok"]
    assert row["first_probed"] == 1000.0 and row["tls_ok"] is True

    for name, pages, expected in (("routes-viewer", ["routes"], 200), ("other-viewer", ["health"], 403)):
        assert owner.post("/api/ui/users", json={"username": name, "password": "test-reader-123",
                                                 "role": "viewer", "pages": pages}).status_code == 201
        viewer = TestClient(app)
        assert viewer.post("/api/login", json={"username": name, "password": "test-reader-123"}).status_code == 200
        assert viewer.get("/api/routes/latest").status_code == expected
    conn.close()
