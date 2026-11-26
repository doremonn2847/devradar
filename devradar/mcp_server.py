"""DevRadar tools for Hermes, served over MCP (stdio)."""
from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timedelta, timezone
from typing import Callable

from devradar.config import expand_path, load_config
from devradar.db import connect, init_schema
from devradar.usage import month_cost

FACT_KEYS = {"deadline", "goal", "note"}
FEEDBACK = {"up", "down", "less"}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Tools:
    def __init__(self, conn: sqlite3.Connection, cfg: dict, clock: Callable[[], datetime] = _utcnow):
        self.conn, self.cfg, self.clock = conn, cfg, clock

    def _find(self, name: str) -> sqlite3.Row | None:
        q = name.strip().casefold()
        for r in self.conn.execute("SELECT * FROM projects"):
            if q in (r["name"].casefold(), (r["display"] or "").casefold()):
                return r
        return None

    def _state(self, row) -> dict:
        st = json.loads(row["state_json"]) if row["state_json"] else {}
        return {k: v for k, v in st.items() if not k.startswith("_")}

    def _last(self, project: str) -> str | None:
        return self.conn.execute("SELECT max(last_event_at) FROM sessions WHERE project=?", (project,)).fetchone()[0]

    def list_projects(self) -> list[dict]:
        """All tracked projects with last activity and the top next step."""
        out = []
        for r in self.conn.execute("SELECT * FROM projects ORDER BY display"):
            nxt = self._state(r).get("next") or []
            out.append({"name": r["name"], "display": r["display"], "last_activity": self._last(r["name"]),
                        "next": nxt[0] if nxt else None})
        return sorted(out, key=lambda p: p["last_activity"] or "", reverse=True)

    def get_project(self, name: str) -> dict:
        """Progress state, pinned facts and open alerts for one project (name or display name)."""
        r = self._find(name)
        if r is None:
            return {"error": f"no project named {name!r}"}
        facts = {f["key"]: f["value"] for f in self.conn.execute("SELECT key, value FROM facts WHERE project=?", (r["name"],))}
        return {"name": r["name"], "display": r["display"], "state": self._state(r), "facts": facts,
                "last_activity": self._last(r["name"]),
                "open_alerts": [a for a in self.get_alerts() if a["project"] == r["name"]]}

    def history(self, project: str, days: int = 7) -> list[dict]:
        """Sessions on a project in the last N days, newest first."""
        r = self._find(project)
        if r is None:
            return []
        cutoff = self.clock() - timedelta(days=days)
        out = []
        for s in self.conn.execute("SELECT * FROM sessions WHERE project=? ORDER BY last_event_at DESC", (r["name"],)):
            if not s["last_event_at"] or datetime.fromisoformat(s["last_event_at"]) < cutoff:
                continue
            prompts = (json.loads(s["record_json"]) if s["record_json"] else {}).get("user_prompts") or [""]
            out.append({"session_id": s["session_id"], "agent": s["agent"], "started_at": s["started_at"],
                        "first_prompt": prompts[0][:200]})
        return out

    def get_alerts(self) -> list[dict]:
        """Unacknowledged alerts."""
        return [dict(a) for a in self.conn.execute(
            "SELECT id, rule, project, reason, created_at FROM alerts WHERE acked=0 ORDER BY id")]

    def ack_alert(self, id: int) -> bool:
        """Mark an alert as handled (call after telling the user)."""
        cur = self.conn.execute("UPDATE alerts SET acked=1, notified=1 WHERE id=?", (id,))
        self.conn.commit()
        return cur.rowcount == 1

    def snooze(self, project: str, days: int) -> str:
        """Silence alerts for a project for N days. Use project '*' for the spread-thin alert."""
        key = "*" if project.strip() == "*" else (self._find(project) or {"name": None})["name"]
        if key is None:
            return f"error: no project named {project!r}"
        until = self.clock() + timedelta(days=int(days))
        self.conn.execute("INSERT OR REPLACE INTO snoozes VALUES(?, ?)", (key, until.isoformat()))
        self.conn.commit()
        return f"ok: snoozed until {until.date().isoformat()}"

    def set_fact(self, project: str, key: str, value: str) -> str:
        """Pin a fact the summarizer must respect. Keys: deadline (YYYY-MM-DD), goal, note."""
        if key not in FACT_KEYS:
            return f"error: key must be one of {sorted(FACT_KEYS)}"
        if key == "deadline":
            try:
                date.fromisoformat(value)
            except ValueError:
                return "error: deadline must be YYYY-MM-DD"
        r = self._find(project)
        if r is None:
            return f"error: no project named {project!r}"
        self.conn.execute("INSERT OR REPLACE INTO facts VALUES(?,?,?)", (r["name"], key, value))
        self.conn.commit()
        return "ok"

    def news_candidates(self) -> list[dict]:
        """News items gathered in the last 24 hours, best first."""
        cutoff = self.clock() - timedelta(hours=24)
        rows = [dict(r) for r in self.conn.execute(
            "SELECT id, source, title, url, summary, kind, score, fetched_at, feedback FROM news_items ORDER BY score DESC")]
        return [r for r in rows if r["fetched_at"] and datetime.fromisoformat(r["fetched_at"]) >= cutoff]

    def news_detail(self, id: int) -> dict:
        """Full stored record for one news item."""
        r = self.conn.execute("SELECT * FROM news_items WHERE id=?", (id,)).fetchone()
        return dict(r) if r else {"error": f"no news item {id}"}

    def news_feedback(self, id: int, signal: str) -> str:
        """Record the user's reaction to a news item: up, down or less."""
        if signal not in FEEDBACK:
            return f"error: signal must be one of {sorted(FEEDBACK)}"
        cur = self.conn.execute("UPDATE news_items SET feedback=? WHERE id=?", (signal, id))
        self.conn.commit()
        return "ok" if cur.rowcount else f"error: no news item {id}"

    def usage_month(self) -> dict:
        """DeepSeek spend this month by the devradar worker, and the soft cap."""
        return {"cost_usd": round(month_cost(self.conn, self.clock()), 4), "cap_usd": self.cfg["cost"]["soft_cap_usd"]}


TOOL_NAMES = ("list_projects", "get_project", "history", "get_alerts", "ack_alert", "snooze", "set_fact",
              "news_candidates", "news_detail", "news_feedback", "usage_month")


def build_server(tools: Tools):
    from mcp.server.mcpserver import MCPServer

    server = MCPServer("devradar", instructions="Progress and news radar for the user's coding projects.")
    for name in TOOL_NAMES:
        server.tool(name=name)(getattr(tools, name))
    return server


def main() -> None:
    cfg = load_config()
    conn = connect(expand_path(cfg["db_path"]))
    init_schema(conn)
    build_server(Tools(conn, cfg)).run("stdio")


if __name__ == "__main__":
    main()
