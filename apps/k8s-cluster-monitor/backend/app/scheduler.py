"""APScheduler wiring. One cron job (if a schedule is configured) runs all
enabled domains in sequence and prunes old data. Configuration lives in the
`schedule` SQLite table, not just env vars, so it can be edited from the
Settings page at runtime without a redeploy.
"""
from __future__ import annotations

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from .checks.registry import DOMAINS, run_domain
from .config import settings
from .db import get_db
from .models import prune_old_runs

_scheduler = BackgroundScheduler()
_JOB_ID = "scheduled-run"


def _run_enabled_domains() -> None:
    conn = get_db()
    row = conn.execute("SELECT enabled_domains FROM schedule WHERE id = 1").fetchone()
    enabled = [d.strip() for d in row[0].split(",")] if row and row[0] else list(DOMAINS)
    for domain in enabled:
        if domain in DOMAINS:
            run_domain(domain, conn)
    prune_old_runs(conn, settings.retention_days)


def apply_schedule(cron_expr: str | None) -> None:
    if _scheduler.get_job(_JOB_ID):
        _scheduler.remove_job(_JOB_ID)
    if cron_expr:
        _scheduler.add_job(_run_enabled_domains, CronTrigger.from_crontab(cron_expr), id=_JOB_ID)


def start() -> None:
    conn = get_db()
    row = conn.execute("SELECT cron FROM schedule WHERE id = 1").fetchone()
    cron_expr = (row[0] if row else None) or (settings.schedule_cron or None)
    if not _scheduler.running:
        _scheduler.start()
    apply_schedule(cron_expr)


def shutdown() -> None:
    if _scheduler.running:
        _scheduler.shutdown(wait=False)
