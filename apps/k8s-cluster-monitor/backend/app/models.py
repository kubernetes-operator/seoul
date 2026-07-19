"""SQLite schema (plain sqlite3 + hand-written SQL — the schema is three
small tables, an ORM would add a dependency without buying much here).

Findings are upserted by fingerprint so history is meaningful:
- new fingerprint this run -> insert with first_seen = last_seen = now
- existing fingerprint, present again -> update last_seen, clear resolved_at
- existing fingerprint, absent this run -> set resolved_at = now (once)
"""
from __future__ import annotations

import sqlite3
import time
from typing import Optional

from .checks.base import CheckResult, Severity

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    domain TEXT NOT NULL,
    started_at REAL NOT NULL,
    finished_at REAL NOT NULL,
    status TEXT NOT NULL,
    status_reasons TEXT,
    critical_count INTEGER NOT NULL,
    warning_count INTEGER NOT NULL,
    info_count INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_runs_domain_started ON runs(domain, started_at);

CREATE TABLE IF NOT EXISTS findings (
    fingerprint TEXT PRIMARY KEY,
    domain TEXT NOT NULL,
    check_id TEXT NOT NULL,
    severity TEXT NOT NULL,
    title TEXT NOT NULL,
    detail TEXT NOT NULL,
    namespace TEXT,
    resource TEXT,
    evidence TEXT,
    reproduce_cmd TEXT,
    first_seen REAL NOT NULL,
    last_seen REAL NOT NULL,
    resolved_at REAL
);
CREATE INDEX IF NOT EXISTS idx_findings_domain ON findings(domain);
CREATE INDEX IF NOT EXISTS idx_findings_resolved ON findings(resolved_at);

CREATE TABLE IF NOT EXISTS schedule (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    cron TEXT,
    enabled_domains TEXT NOT NULL DEFAULT 'health,network,security,storage_backup,observability'
);

-- Basic Auth credential, PBKDF2-HMAC hash + random salt (never plaintext).
-- Seeded once from the K8s Secret's env vars on first boot (see auth.py);
-- authoritative after that, so it can be changed from the dashboard without
-- this app ever needing `secrets` RBAC to write back to the Secret.
CREATE TABLE IF NOT EXISTS auth_credential (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    username TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    salt TEXT NOT NULL,
    updated_at REAL NOT NULL
);
"""


def get_connection(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    conn.execute("INSERT OR IGNORE INTO schedule (id, cron) VALUES (1, NULL)")
    conn.commit()
    return conn


def save_run(conn: sqlite3.Connection, result: CheckResult, started_at: float, finished_at: float) -> int:
    counts = result.summary_counts()
    cur = conn.execute(
        """INSERT INTO runs (domain, started_at, finished_at, status, status_reasons,
                              critical_count, warning_count, info_count)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            result.domain,
            started_at,
            finished_at,
            result.status,
            "; ".join(result.status_reasons) if result.status_reasons else None,
            counts[Severity.CRITICAL.value],
            counts[Severity.WARNING.value],
            counts[Severity.INFO.value],
        ),
    )
    run_id = cur.lastrowid

    now = finished_at
    seen_fingerprints = set()
    for finding in result.findings:
        fp = finding.fingerprint
        seen_fingerprints.add(fp)
        existing = conn.execute("SELECT fingerprint FROM findings WHERE fingerprint = ?", (fp,)).fetchone()
        if existing:
            conn.execute(
                """UPDATE findings SET severity=?, title=?, detail=?, evidence=?, reproduce_cmd=?,
                                        last_seen=?, resolved_at=NULL
                   WHERE fingerprint=?""",
                (finding.severity.value, finding.title, finding.detail, finding.evidence,
                 finding.reproduce_cmd, now, fp),
            )
        else:
            conn.execute(
                """INSERT INTO findings (fingerprint, domain, check_id, severity, title, detail,
                                          namespace, resource, evidence, reproduce_cmd,
                                          first_seen, last_seen, resolved_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)""",
                (fp, finding.domain, finding.check_id, finding.severity.value, finding.title,
                 finding.detail, finding.namespace, finding.resource, finding.evidence,
                 finding.reproduce_cmd, now, now),
            )

    # Anything for this domain not seen in this run, and not already marked
    # resolved, is resolved as of now.
    rows = conn.execute(
        "SELECT fingerprint FROM findings WHERE domain = ? AND resolved_at IS NULL", (result.domain,)
    ).fetchall()
    for (fp,) in rows:
        if fp not in seen_fingerprints:
            conn.execute("UPDATE findings SET resolved_at = ? WHERE fingerprint = ?", (now, fp))

    conn.commit()
    return run_id


def prune_old_runs(conn: sqlite3.Connection, retention_days: int) -> None:
    cutoff = time.time() - retention_days * 86400
    conn.execute("DELETE FROM runs WHERE started_at < ?", (cutoff,))
    # Long-resolved findings are pruned too, so the table doesn't grow forever;
    # unresolved findings are kept regardless of age (still-open issues matter).
    conn.execute("DELETE FROM findings WHERE resolved_at IS NOT NULL AND resolved_at < ?", (cutoff,))
    conn.commit()
