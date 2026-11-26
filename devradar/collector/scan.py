"""Scan agent session roots, parse new lines, and track which sessions have finished."""
from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from devradar.collector.claude_code import parse_claude_line
from devradar.collector.codex import parse_codex_line
from devradar.collector.repo import resolve_project
from devradar.collector.tail import read_new_lines
from devradar.config import expand_path
from devradar.models import SessionRecord

log = logging.getLogger(__name__)
PARSERS = {"claude_code": parse_claude_line, "codex": parse_codex_line}


def _load_record(conn, path: str, agent: str) -> SessionRecord:
    row = conn.execute("SELECT record_json FROM sessions WHERE path=?", (path,)).fetchone()
    if row and row["record_json"]:
        return SessionRecord.from_json_dict(json.loads(row["record_json"]))
    return SessionRecord(agent=agent, session_id="", path=path)


def _ingest_file(conn: sqlite3.Connection, agent: str, path: Path) -> None:
    key = str(path)
    size = path.stat().st_size
    row = conn.execute("SELECT offset, size FROM files WHERE path=?", (key,)).fetchone()
    offset = row["offset"] if row else 0
    if row and row["size"] == size and offset == size:
        return
    lines, new_offset = read_new_lines(path, offset)
    if new_offset < offset:  # file was rewritten: start the record over
        conn.execute("DELETE FROM sessions WHERE path=?", (key,))
    conn.execute(
        "INSERT INTO files(path, offset, size, agent) VALUES(?,?,?,?) "
        "ON CONFLICT(path) DO UPDATE SET offset=excluded.offset, size=excluded.size",
        (key, new_offset, size, agent),
    )
    if not lines:
        return
    rec = _load_record(conn, key, agent)
    parse = PARSERS[agent]
    for line in lines:
        try:
            parse(line, rec)
        except Exception:  # one odd line must not lose the rest of the file
            log.warning("unparseable line in %s", path, exc_info=True)
    if not rec.session_id:
        rec.session_id = path.stem
    project, display = resolve_project(rec.cwd)
    conn.execute("INSERT OR IGNORE INTO projects(name, display) VALUES(?,?)", (project, display))
    conn.execute(
        "INSERT INTO sessions(session_id, agent, project, path, started_at, last_event_at, finished, record_json) "
        "VALUES(?,?,?,?,?,?,0,?) ON CONFLICT(session_id) DO UPDATE SET project=excluded.project, "
        "path=excluded.path, started_at=excluded.started_at, last_event_at=excluded.last_event_at, "
        "finished=0, record_json=excluded.record_json",
        (rec.session_id, agent, project, key,
         rec.started_at.isoformat() if rec.started_at else None,
         rec.last_event_at.isoformat() if rec.last_event_at else None,
         json.dumps(rec.to_json_dict(), ensure_ascii=False)),
    )


def collect(conn: sqlite3.Connection, cfg: dict, now: datetime) -> list[str]:
    """Ingest new session lines; return ids of sessions that became finished in this pass."""
    for agent, root in cfg["collector"]["roots"].items():
        if agent not in PARSERS:
            continue
        base = expand_path(root)
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*.jsonl")):
            if _is_sidechain_file(path, base):
                continue
            try:
                _ingest_file(conn, agent, path)
                conn.commit()  # per file: never hold the write lock across the whole scan
            except Exception:  # file vanished/locked, or bad data: drop this file's changes, retry next pass
                conn.rollback()
                log.warning("skip %s", path, exc_info=True)
    cutoff = now - timedelta(minutes=cfg["collector"]["finished_after_min"])
    finished = []
    for row in conn.execute(
        "SELECT session_id, last_event_at FROM sessions WHERE finished=0 AND last_event_at IS NOT NULL"
    ).fetchall():
        t = datetime.fromisoformat(row["last_event_at"])
        if t.tzinfo is None:  # rows written before timestamps were normalised
            t = t.replace(tzinfo=timezone.utc)
        if t <= cutoff:
            finished.append((t, row["session_id"]))
    finished.sort()  # oldest first, so the newest session has the last word on project state
    conn.executemany("UPDATE sessions SET finished=1 WHERE session_id=?", [(s,) for _, s in finished])
    conn.commit()
    return [s for _, s in finished]


def _is_sidechain_file(path: Path, base: Path) -> bool:
    """Claude Code subagent transcripts: <sid>/subagents/*.jsonl or legacy agent-*.jsonl siblings."""
    return "subagents" in path.relative_to(base).parts or path.name.startswith("agent-")
