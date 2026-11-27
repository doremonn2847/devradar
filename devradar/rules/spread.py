"""Spread thin: many projects active, none moving anything to done."""
from __future__ import annotations

from datetime import timedelta

from devradar.models import Alert
from devradar.rules.base import projects, sessions_since


def check(conn, now, cfg) -> list[Alert]:
    rows = sessions_since(conn, now - timedelta(days=cfg["rules"]["spread_window_days"]))
    active = {r["project"] for r in rows}
    if len(active) < cfg["rules"]["spread_min_projects"]:
        return []
    recent = {r["session_id"] for r in rows}
    for name, _, st in projects(conn):
        if name in active and any(d.get("session_id") in recent for d in st.get("done") or []):
            return []
    days = cfg["rules"]["spread_window_days"]
    return [Alert("spread", "*", f"{len(active)} projects active in {days} days and none finished anything", now)]
