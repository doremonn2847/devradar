"""Grounded per-project state updates from session excerpts."""
from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import asdict
from datetime import datetime, timezone

from devradar.llm import LLMClient
from devradar.models import ProjectState

log = logging.getLogger(__name__)

SYSTEM = """You maintain a short progress record for one software project.
You receive the current state, one finished coding-agent session excerpt, its session_id,
and facts the user pinned. Return ONLY a JSON object with keys:
goal (string), done (list of {"item", "session_id"}), next (list of strings),
blockers (list of strings), deadlines (list of {"item", "date": "YYYY-MM-DD"}).
Rules:
- Use only what the excerpt or current state shows. Never invent progress.
- Every NEW done item must have "session_id" equal to the given session_id.
- Keep each item under 15 words. At most 5 next items and 5 blockers.
- Remove next/blocker items the excerpt shows are resolved."""


def _load_state(conn, project: str) -> tuple[ProjectState, dict]:
    row = conn.execute("SELECT state_json FROM projects WHERE name=?", (project,)).fetchone()
    raw = json.loads(row["state_json"]) if row and row["state_json"] else {}
    extra = {k: v for k, v in raw.items() if k.startswith("_")}
    st = ProjectState(**{k: v for k, v in raw.items() if not k.startswith("_")})
    return st, extra


def _valid(d: dict) -> bool:
    def strs(x):
        return isinstance(x, list) and all(isinstance(i, str) for i in x)

    def dicts(x, keys):
        return isinstance(x, list) and all(isinstance(i, dict) and keys <= i.keys() for i in x)

    return (isinstance(d.get("goal"), str) and isinstance(d.get("done"), list)
            and all(isinstance(i, dict) and "item" in i for i in d["done"])
            and strs(d.get("next")) and strs(d.get("blockers"))
            and dicts(d.get("deadlines"), {"item", "date"}))


def update_project_state(conn: sqlite3.Connection, project: str, session_id: str,
                         llm: LLMClient) -> ProjectState | None:
    current, extra = _load_state(conn, project)
    row = conn.execute("SELECT excerpt FROM sessions WHERE session_id=?", (session_id,)).fetchone()
    excerpt = row["excerpt"] if row and row["excerpt"] else ""
    facts = {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM facts WHERE project=?", (project,))}
    payload = json.dumps({"current_state": asdict(current), "session_id": session_id,
                          "excerpt": excerpt, "pinned_facts": facts}, ensure_ascii=False)
    data = None
    for _ in range(2):
        try:
            d = llm.complete_json(SYSTEM, payload)
        except ValueError as e:
            log.warning("state update for %s failed: %s", project, e)
            continue
        if _valid(d):
            data = d
            break
        log.warning("state update for %s: invalid shape", project)
    if data is None:
        return None

    known = {r["session_id"] for r in conn.execute("SELECT session_id FROM sessions WHERE project=?", (project,))}
    done = list(current.done)
    seen = {(d["item"], d.get("session_id")) for d in done}
    for d in data["done"]:
        key = (d.get("item"), d.get("session_id"))
        if d.get("session_id") in known and key not in seen:
            done.append({"item": d["item"], "session_id": d["session_id"]})
            seen.add(key)

    st = ProjectState(goal=facts.get("goal", data["goal"]), done=done, next=data["next"][:5],
                      blockers=data["blockers"][:5], deadlines=data["deadlines"])
    if "deadline" in facts:
        st.deadlines = [{"item": "pinned deadline", "date": facts["deadline"]}]

    now = datetime.now(timezone.utc).isoformat()
    if st.next != current.next or "_next_since" not in extra:
        extra["_next_since"] = now
    conn.execute("UPDATE projects SET state_json=?, updated_at=? WHERE name=?",
                 (json.dumps({**asdict(st), **extra}, ensure_ascii=False), now, project))
    conn.commit()
    return st
