"""Deadline drift: a deadline is close and the project has gone quiet."""
from __future__ import annotations

from datetime import date, timedelta

from devradar.models import Alert
from devradar.rules.base import last_activity, projects


def check(conn, now, cfg) -> list[Alert]:
    window = cfg["rules"]["deadline_window_days"]
    idle = timedelta(days=cfg["rules"]["deadline_idle_days"])
    today = now.date()
    out = []
    for name, disp, st in projects(conn):
        last = last_activity(conn, name)
        if last is None or now - last < idle:
            continue
        for d in st.get("deadlines") or []:
            try:
                due = date.fromisoformat(str(d.get("date")))
            except ValueError:
                continue
            left = (due - today).days
            if 0 <= left <= window:
                out.append(Alert("deadline", name,
                                 f"{disp}: \"{d.get('item')}\" due in {left} days, idle {(now - last).days} days", now))
                break
    return out
