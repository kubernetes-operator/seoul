from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles

from . import scheduler
from .api import routes_auth, routes_findings, routes_loadtest, routes_meta, routes_routes, routes_runs, routes_schedule
from .config import settings
from .db import get_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Open the DB now so default users (admin/password) are seeded at startup;
    # get_db() does the seeding on first open (idempotent). See db.py / auth.py.
    get_db()
    scheduler.start()
    yield
    scheduler.shutdown()


app = FastAPI(title="k8s-cluster-monitor", lifespan=lifespan)


@app.middleware("http")
async def no_store_api_responses(request: Request, call_next):
    """Authenticated API responses must never be cached by the browser or any
    intermediary (Cloudflare fronts this app). Without an explicit directive a
    browser may heuristically cache a GET /api response and later serve a stale
    body — or, worse for auth UX, a stale 401 — so force no-store on /api. The
    login-session roundtrip itself was verified healthy through the CDN; this
    is defense-in-depth against caching-induced spurious re-logins."""
    response = await call_next(request)
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    return response


app.include_router(routes_meta.router)
app.include_router(routes_runs.router, prefix="/api")
app.include_router(routes_findings.router, prefix="/api")
app.include_router(routes_schedule.router, prefix="/api")
app.include_router(routes_auth.router, prefix="/api")
app.include_router(routes_loadtest.router, prefix="/api")
app.include_router(routes_routes.router, prefix="/api")

_static_dir = Path(settings.static_dir)
if not _static_dir.exists():
    # Local dev fallback: repo-relative frontend/static when the container's
    # absolute path doesn't exist on this machine.
    _dev_static = Path(__file__).resolve().parent.parent.parent / "frontend" / "static"
    if _dev_static.exists():
        _static_dir = _dev_static

if _static_dir.exists():
    app.mount("/", StaticFiles(directory=str(_static_dir), html=True), name="static")
