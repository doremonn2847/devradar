"""Dangling session: ended mid-task and not resumed."""
from __future__ import annotations

from datetime import timedelta

from devradar.models import Alert
from devradar.rules.base import display, dt, last_activity, record

MAX_AGE = timedelta(days=7)  # older sessions are covered by the stalled rule


def check(conn, now, cfg) -> list[Alert]:
    min_age = timedelta(hours=cfg["rules"]["dangling_hours"])
    out = []
    for r in conn.execute("SELECT * FROM sessions WHERE finished=1 AND last_event_at IS NOT NULL"):
        end = dt(r["last_event_at"])
        if not (min_age <= now - end <= MAX_AGE):
            continue
        if last_activity(conn, r["project"]) > end:
            continue  # resumed (or new work) on the same project
        rec = record(r)
        todos = rec.get("open_todos") or []
        if todos:
            reason = f"open todo \"{todos[0]}\""
        elif rec.get("ended_on_question"):
            reason = "the agent asked you a question and got no reply"
        else:
            continue
        hours = int((now - end).total_seconds() // 3600)
        out.append(Alert("dangling", r["project"], f"{display(conn, r['project'])}: session ended {hours}h ago with {reason}", now))
    return out
