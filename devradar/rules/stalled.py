"""Stalled next step: 'next' unchanged and no sessions for N days."""
from __future__ import annotations

from datetime import timedelta

from devradar.models import Alert
from devradar.rules.base import dt, last_activity, projects


def check(conn, now, cfg) -> list[Alert]:
    n = cfg["rules"]["stalled_days"]
    cutoff = now - timedelta(days=n)
    oldest = now - timedelta(days=cfg["rules"]["stalled_max_days"])  # past this, treat as abandoned
    out = []
    for name, disp, st in projects(conn):
        nxt = st.get("next") or []
        since = dt(st.get("_next_since"))
        last = last_activity(conn, name)
        if nxt and last and oldest <= last <= cutoff and (since is None or since <= cutoff):
            days = (now - last).days
            out.append(Alert("stalled", name, f"{disp}: no sessions for {days} days; next step still \"{nxt[0]}\"", now))
    return out
