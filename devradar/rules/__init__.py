"""'Something's off' rules: plain code, deterministic."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta

from devradar.models import Alert
from devradar.rules import dangling, deadline, loop, spread, stalled
from devradar.rules.base import dt, normalize_error  # noqa: F401  (re-export)

RULES = (stalled, deadline, loop, dangling, spread)


def should_emit(conn: sqlite3.Connection, rule: str, project: str, now: datetime, cooldown_days: int) -> bool:
    s = conn.execute("SELECT until FROM snoozes WHERE project=?", (project,)).fetchone()
    if s and dt(s["until"]) > now:
        return False
    cutoff = now - timedelta(days=cooldown_days)
    for r in conn.execute("SELECT created_at FROM alerts WHERE rule=? AND project=?", (rule, project)):
        if dt(r["created_at"]) > cutoff:
            return False
    return True


def store_alerts(conn: sqlite3.Connection, alerts: list[Alert], now: datetime, cooldown_days: int) -> list[Alert]:
    kept = []
    for a in alerts:
        if should_emit(conn, a.rule, a.project, now, cooldown_days):
            conn.execute("INSERT INTO alerts(rule, project, reason, created_at) VALUES(?,?,?,?)",
                         (a.rule, a.project, a.reason, a.created_at.isoformat()))
            kept.append(a)
    conn.commit()
    return kept


def evaluate_rules(conn: sqlite3.Connection, now: datetime, cfg: dict) -> list[Alert]:
    found = []
    for rule in RULES:
        found.extend(rule.check(conn, now, cfg))
    return store_alerts(conn, found, now, cfg["rules"]["cooldown_days"])
