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
-- 권한은 별도 테이블에 추가한다. 기존 비밀번호/사용자 열은 재작성하지 않는다.
CREATE TABLE IF NOT EXISTS user_access (
    username TEXT PRIMARY KEY,
    role TEXT NOT NULL,
    pages TEXT
);

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

-- Legacy single Basic Auth credential (pre-v0.3.0). Kept only so ensure_seed()
-- in auth.py can migrate an existing row into the multi-user `users` table on
-- upgrade; no longer written to. See auth.py.
CREATE TABLE IF NOT EXISTS auth_credential (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    username TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    salt TEXT NOT NULL,
    updated_at REAL NOT NULL
);

-- Multi-user login credentials (v0.3.0+). PBKDF2-HMAC hash + per-user random
-- salt, never plaintext. Seeded with the default admin/password on first boot
-- against an empty table (see auth.ensure_seed) — stored in SQLite, not K8s
-- Secrets, so add-user / change-password stay self-service without this app
-- ever needing `secrets` RBAC.
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    salt TEXT NOT NULL,
    created_at REAL NOT NULL
);

-- Server-side login sessions: opaque token in an httponly cookie. No signing
-- key to manage, individually revocable (logout), and survive pod restarts
-- (SQLite on the PVC). Expired rows are pruned lazily on lookup.
CREATE TABLE IF NOT EXISTS sessions (
    token TEXT PRIMARY KEY,
    username TEXT NOT NULL,
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sessions_expires ON sessions(expires_at);

-- Active load-test results. Doesn't fit the Finding/CheckResult shape (a
-- distribution/rate result, not a discrete issue) — see load_test.py.
-- A summary Finding is ALSO written via save_run() under domain="load_test"
-- so results surface in the unified findings/trend view too.
CREATE TABLE IF NOT EXISTS load_tests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    target TEXT NOT NULL,
    namespace TEXT NOT NULL,
    concurrency INTEGER NOT NULL,
    duration_seconds REAL NOT NULL,
    total_requests INTEGER NOT NULL,
    error_count INTEGER NOT NULL,
    p50_ms REAL,
    p95_ms REAL,
    p99_ms REAL,
    achieved_rps REAL NOT NULL,
    triggered_by TEXT NOT NULL,
    status TEXT NOT NULL,
    status_detail TEXT,
    started_at REAL NOT NULL,
    finished_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_load_tests_started ON load_tests(started_at);

-- Domains & pages probe rows: one per discovered (hostname, path) per
-- "routes" run. Detail behind the findings, for the per-URL status table.
CREATE TABLE IF NOT EXISTS route_probes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL,
    started_at REAL NOT NULL,
    host TEXT NOT NULL,
    path TEXT NOT NULL,
    namespace TEXT,
    route TEXT,
    route_created REAL,
    accepted INTEGER,
    state TEXT NOT NULL,
    status_code INTEGER,
    latency_ms REAL,
    error TEXT,
    tls_ok INTEGER,
    tls_error TEXT,
    cert_not_after REAL
);
CREATE INDEX IF NOT EXISTS idx_route_probes_run ON route_probes(run_id);
CREATE INDEX IF NOT EXISTS idx_route_probes_target ON route_probes(host, path, started_at);
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
    conn.execute("DELETE FROM load_tests WHERE started_at < ?", (cutoff,))
    conn.execute("DELETE FROM route_probes WHERE started_at < ?", (cutoff,))
    conn.commit()


def save_load_test(conn: sqlite3.Connection, row: dict) -> int:
    cur = conn.execute(
        """INSERT INTO load_tests (target, namespace, concurrency, duration_seconds, total_requests,
                                    error_count, p50_ms, p95_ms, p99_ms, achieved_rps, triggered_by,
                                    status, status_detail, started_at, finished_at)
           VALUES (:target, :namespace, :concurrency, :duration_seconds, :total_requests,
                   :error_count, :p50_ms, :p95_ms, :p99_ms, :achieved_rps, :triggered_by,
                   :status, :status_detail, :started_at, :finished_at)""",
        row,
    )
    conn.commit()
    return cur.lastrowid


def list_load_tests(conn: sqlite3.Connection, limit: int = 50) -> list[dict]:
    rows = conn.execute("SELECT * FROM load_tests ORDER BY started_at DESC LIMIT ?", (limit,)).fetchall()
    columns = [c[0] for c in conn.execute("SELECT * FROM load_tests LIMIT 0").description]
    return [dict(zip(columns, row)) for row in rows]


_PROBE_COLS = ("host", "path", "namespace", "route", "route_created", "accepted", "state",
               "status_code", "latency_ms", "error", "tls_ok", "tls_error", "cert_not_after")


def save_route_probes(conn: sqlite3.Connection, run_id: int, started_at: float, probes: list[dict]) -> None:
    conn.executemany(
        f"INSERT INTO route_probes (run_id, started_at, {', '.join(_PROBE_COLS)}) "
        f"VALUES (?, ?, {', '.join('?' for _ in _PROBE_COLS)})",
        [(run_id, started_at, *(p.get(c) for c in _PROBE_COLS)) for p in probes],
    )
    conn.commit()


def latest_route_probes(conn: sqlite3.Connection, history: int = 10) -> dict:
    """Rows of the most recent routes run, each with its last `history`
    states (oldest -> newest) and the first time this URL was ever probed."""
    run = conn.execute(
        "SELECT id, started_at FROM runs WHERE domain = 'routes' ORDER BY started_at DESC LIMIT 1"
    ).fetchone()
    if not run:
        return {"run_id": None, "started_at": None, "probes": []}
    cur = conn.execute(f"SELECT {', '.join(_PROBE_COLS)} FROM route_probes WHERE run_id = ? ORDER BY host, path",
                       (run[0],))
    rows = [dict(zip(_PROBE_COLS, r)) for r in cur.fetchall()]
    for row in rows:
        hist = conn.execute(
            "SELECT state, status_code, started_at FROM route_probes WHERE host = ? AND path = ? "
            "ORDER BY started_at DESC LIMIT ?", (row["host"], row["path"], history)).fetchall()
        row["history"] = [{"state": s, "status_code": c, "at": t} for s, c, t in reversed(hist)]
        row["first_probed"] = conn.execute(
            "SELECT MIN(started_at) FROM route_probes WHERE host = ? AND path = ?",
            (row["host"], row["path"])).fetchone()[0]
        for key in ("accepted", "tls_ok"):
            if row[key] is not None:
                row[key] = bool(row[key])
    return {"run_id": run[0], "started_at": run[1], "probes": rows}
