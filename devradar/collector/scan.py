"""Scan agent session roots, parse new data, link orchestrated sessions, and track finished sessions."""
from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from devradar.collector.agy_app import read_agy_app_dir, signature
from devradar.collector.agy_cli import parse_agy_line
from devradar.collector.claude_code import parse_claude_line
from devradar.collector.codex import parse_codex_line
from devradar.collector.repo import resolve_project
from devradar.collector.tail import read_new_lines
from devradar.config import expand_path
from devradar.models import SessionRecord

log = logging.getLogger(__name__)
LINE_PARSERS = {"claude_code": parse_claude_line, "codex": parse_codex_line, "agy_cli": parse_agy_line}
# An agy conversation belongs to the launch closest before it, within this window.
LINK_BEFORE, LINK_AFTER = timedelta(minutes=30), timedelta(minutes=2)


def _load_record(conn, path: str, agent: str) -> SessionRecord:
    row = conn.execute("SELECT record_json FROM sessions WHERE path=?", (path,)).fetchone()
    if row and row["record_json"]:
        return SessionRecord.from_json_dict(json.loads(row["record_json"]))
    return SessionRecord(agent=agent, session_id="", path=path)


def _save_session(conn, rec: SessionRecord, agent: str, key: str) -> None:
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


def _upsert_file(conn, key: str, offset: int, size: int, agent: str) -> None:
    conn.execute(
        "INSERT INTO files(path, offset, size, agent) VALUES(?,?,?,?) "
        "ON CONFLICT(path) DO UPDATE SET offset=excluded.offset, size=excluded.size",
        (key, offset, size, agent),
    )


def _ingest_jsonl(conn: sqlite3.Connection, agent: str, path: Path, sid_hint: str | None = None) -> None:
    key = str(path)
    size = path.stat().st_size
    row = conn.execute("SELECT offset, size FROM files WHERE path=?", (key,)).fetchone()
    offset = row["offset"] if row else 0
    if row and row["size"] == size and offset == size:
        return
    lines, new_offset = read_new_lines(path, offset)
    if new_offset < offset:  # file was rewritten: start the record over
        conn.execute("DELETE FROM sessions WHERE path=?", (key,))
    _upsert_file(conn, key, new_offset, size, agent)
    if not lines:
        return
    rec = _load_record(conn, key, agent)
    parse = LINE_PARSERS[agent]
    for line in lines:
        try:
            parse(line, rec)
        except Exception:  # one odd line must not lose the rest of the file
            log.warning("unparseable line in %s", path, exc_info=True)
    if not rec.session_id:
        rec.session_id = sid_hint or path.stem
    _save_session(conn, rec, agent, key)


def _ingest_agy_app(conn: sqlite3.Connection, d: Path) -> None:
    key = str(d)
    mtime_ns, size = signature(d)
    row = conn.execute("SELECT offset, size FROM files WHERE path=?", (key,)).fetchone()
    if row and row["offset"] == mtime_ns and row["size"] == size:
        return
    _upsert_file(conn, key, mtime_ns, size, "agy_app")
    _save_session(conn, read_agy_app_dir(d), "agy_app", key)


def _roots(value) -> list[Path]:
    values = value if isinstance(value, list) else [value]
    return [p for p in (expand_path(v) for v in values) if p.is_dir()]


def _units(agent: str, base: Path):
    """Yield (ingest_fn, args) for every session unit under one root."""
    if agent in ("claude_code", "codex"):
        for path in sorted(base.rglob("*.jsonl")):
            if agent == "claude_code" and _is_sidechain_file(path, base):
                continue
            yield _ingest_jsonl, (agent, path)
    elif agent == "agy_cli":
        for path in sorted(base.glob("*/.system_generated/logs/transcript.jsonl")):
            yield _ingest_jsonl, (agent, path, path.parents[2].name)


def _agy_app_dirs(bases: list[Path]) -> list[Path]:
    """One folder per conversation id; when upgrades left copies in several roots, the newest wins."""
    best: dict[str, tuple[int, Path]] = {}
    for base in bases:
        for d in base.iterdir():
            if d.is_dir() and any(f.suffix == ".md" for f in d.iterdir()):
                m = signature(d)[0]
                if d.name not in best or m > best[d.name][0]:
                    best[d.name] = (m, d)
    return [d for _, d in sorted(best.values(), key=lambda x: str(x[1]))]


def _run(conn, fn, args) -> None:
    try:
        fn(conn, *args)
        conn.commit()  # per unit: never hold the write lock across the whole scan
    except Exception:  # vanished/locked file or bad data: drop this unit's changes, retry next pass
        conn.rollback()
        log.warning("skip %s", args[-1] if args else fn, exc_info=True)


def link_orchestrated(conn: sqlite3.Connection) -> None:
    """Give each unfinished agy CLI session the project of the session that launched it."""
    agy = conn.execute("SELECT session_id, started_at FROM sessions "
                       "WHERE agent='agy_cli' AND finished=0 AND started_at IS NOT NULL").fetchall()
    for a in agy:
        start = datetime.fromisoformat(a["started_at"])
        lo, hi = (start - LINK_BEFORE).isoformat(), (start + LINK_AFTER).isoformat()
        best = None
        for p in conn.execute("SELECT project, record_json FROM sessions WHERE agent IN ('claude_code','codex') "
                              "AND last_event_at >= ? AND started_at <= ?", (lo, hi)):
            for t in json.loads(p["record_json"] or "{}").get("agy_launches") or []:
                lt = datetime.fromisoformat(t)
                if start - LINK_BEFORE <= lt <= start + LINK_AFTER:
                    gap = abs((start - lt).total_seconds())
                    if best is None or gap < best[0]:
                        best = (gap, p["project"])
        if best:
            conn.execute("UPDATE sessions SET project=? WHERE session_id=?", (best[1], a["session_id"]))
    conn.commit()


def collect(conn: sqlite3.Connection, cfg: dict, now: datetime) -> list[str]:
    """Ingest new session data; return ids of sessions that became finished in this pass."""
    roots = cfg["collector"]["roots"]
    for agent, value in roots.items():
        if agent == "agy_app":
            for d in _agy_app_dirs(_roots(value)):
                _run(conn, _ingest_agy_app, (d,))
            continue
        for base in _roots(value):
            for fn, args in _units(agent, base):
                _run(conn, fn, args)
    link_orchestrated(conn)
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
