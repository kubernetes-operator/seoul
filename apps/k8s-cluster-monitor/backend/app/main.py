from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
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


_SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "same-origin",
    # Script/style sources are left open on purpose (the shared web-standard
    # layer injects inline styles); these directives only forbid framing,
    # plugins and <base> hijacking, which nothing in this app uses.
    "Content-Security-Policy": "frame-ancestors 'none'; object-src 'none'; base-uri 'self'; form-action 'self'",
}


@app.middleware("http")
async def security_guards(request: Request, call_next):
    """Cross-origin write guard + baseline security headers.

    SameSite=Lax treats every *.studiobasa.com app as "same-site", so a
    state-changing /api call carrying our cookie could still be triggered
    from a sibling app on another subdomain. Browsers always send Origin on
    cross-origin POST/PUT/PATCH/DELETE, so reject those whose Origin host
    doesn't match the host the request was sent to."""
    if request.method not in ("GET", "HEAD", "OPTIONS") and request.url.path.startswith("/api/"):
        origin = request.headers.get("origin")
        if origin and urlsplit(origin).netloc.lower() != request.headers.get("host", "").lower():
            return JSONResponse({"detail": "다른 출처의 요청은 허용되지 않습니다"}, status_code=403)
    response = await call_next(request)
    for key, value in _SECURITY_HEADERS.items():
        response.headers.setdefault(key, value)
    if request.headers.get("x-forwarded-proto", "").split(",")[0].strip() == "https":
        response.headers.setdefault("Strict-Transport-Security", "max-age=15552000")
    return response


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
