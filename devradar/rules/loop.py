"""Loop: the same error keeps coming back across sessions."""
from __future__ import annotations

from collections import defaultdict
from datetime import timedelta

from devradar.models import Alert
from devradar.rules.base import display, normalize_error, record, sessions_since


def check(conn, now, cfg) -> list[Alert]:
    need = cfg["rules"]["loop_min_sessions"]
    rows = sessions_since(conn, now - timedelta(days=cfg["rules"]["loop_window_days"]))
    seen: dict[tuple[str, str], set[str]] = defaultdict(set)
    sample: dict[tuple[str, str], str] = {}
    for r in rows:
        for e in record(r).get("errors") or []:
            key = (r["project"], normalize_error(e))
            seen[key].add(r["session_id"])
            sample.setdefault(key, e)
    out, flagged = [], set()
    for (proj, sig), sids in sorted(seen.items()):
        if len(sids) >= need and proj not in flagged:
            flagged.add(proj)
            out.append(Alert("loop", proj, f"{display(conn, proj)}: same error in {len(sids)} sessions: "
                                           f"{sample[(proj, sig)][:100]}", now))
    return out
