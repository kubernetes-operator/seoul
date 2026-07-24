from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .. import load_test
from ..auth import require_session
from ..config import settings
from ..db import get_db
from ..models import list_load_tests, save_load_test, save_run

router = APIRouter(dependencies=[Depends(require_session)])


class LoadTestRequest(BaseModel):
    namespace: str
    service: str
    port: int
    path: str = "/"
    concurrency: int = Field(default=5, ge=1)
    duration_seconds: float = Field(default=10.0, gt=0)


@router.get("/loadtest/targets")
def get_targets():
    return {
        "enabled": settings.loadtest_enabled,
        "allowed_targets": load_test.ALLOWED_TARGETS,
        "max_concurrency": settings.loadtest_max_concurrency,
        "max_duration_seconds": settings.loadtest_max_duration_seconds,
        "max_total_requests": settings.loadtest_max_total_requests,
    }


@router.get("/loadtest/history")
def get_history(limit: int = 50):
    return list_load_tests(get_db(), limit)


@router.post("/loadtest")
async def trigger_load_test(body: LoadTestRequest, current_user: str = Depends(require_session)):
    params = load_test.LoadTestParams(
        namespace=body.namespace, service=body.service, port=body.port,
        path=body.path, concurrency=body.concurrency, duration_seconds=body.duration_seconds,
    )
    try:
        result = await load_test.execute(params, triggered_by=current_user)
    except load_test.LoadTestError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc

    conn = get_db()
    load_test_id = save_load_test(conn, result)
    summary = load_test.build_summary_check_result(result)
    run_id = save_run(conn, summary, result["started_at"], result["finished_at"])

    return {**result, "load_test_id": load_test_id, "run_id": run_id}
