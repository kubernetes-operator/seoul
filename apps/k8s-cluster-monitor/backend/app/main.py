from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from . import scheduler
from .api import routes_auth, routes_findings, routes_loadtest, routes_meta, routes_runs, routes_schedule
from .config import settings


@asynccontextmanager
async def lifespan(app: FastAPI):
    scheduler.start()
    yield
    scheduler.shutdown()


app = FastAPI(title="k8s-cluster-monitor", lifespan=lifespan)

app.include_router(routes_meta.router)
app.include_router(routes_runs.router, prefix="/api")
app.include_router(routes_findings.router, prefix="/api")
app.include_router(routes_schedule.router, prefix="/api")
app.include_router(routes_auth.router, prefix="/api")
app.include_router(routes_loadtest.router, prefix="/api")

_static_dir = Path(settings.static_dir)
if not _static_dir.exists():
    # Local dev fallback: repo-relative frontend/static when the container's
    # absolute path doesn't exist on this machine.
    _dev_static = Path(__file__).resolve().parent.parent.parent / "frontend" / "static"
    if _dev_static.exists():
        _static_dir = _dev_static

if _static_dir.exists():
    app.mount("/", StaticFiles(directory=str(_static_dir), html=True), name="static")
