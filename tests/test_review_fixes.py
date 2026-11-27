"""Regression tests for findings from the final review."""
import json
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from devradar.collector.common import parse_ts
from devradar.collector.scan import collect
from devradar.config import load_config
from devradar.db import connect, init_schema
from devradar.rules import stalled
from devradar.state.project_state import update_project_state
from devradar.usage import record_usage
from devradar.worker import run_cycle, trigger_hermes

FIX = Path(__file__).parent / "fixtures"
UTC = timezone.utc
LAST = datetime(2026, 11, 20, 10, 4, tzinfo=UTC)


def _env(tmp_path):
    root = tmp_path / "claude" / "proj"
    root.mkdir(parents=True)
    shutil.copy(FIX / "claude_session.jsonl", root / "s-abc.jsonl")
    cfg = load_config(tmp_path / "none.yaml")
    cfg["collector"]["roots"] = {"claude_code": str(tmp_path / "claude")}
    conn = connect(tmp_path / "d.db")
    init_schema(conn)
    return conn, cfg, root


def _line(sid, ts, text, kind="user", **extra):
    content = text if kind == "user" else [{"type": "text", "text": text}]
    return json.dumps({"type": kind, "sessionId": sid, "cwd": "/x/other", "timestamp": ts,
                       "message": {"role": kind, "content": content}, **extra}) + "\n"


# C1
def test_subagent_transcript_does_not_overwrite_parent(tmp_path):
    conn, cfg, root = _env(tmp_path)
    collect(conn, cfg, LAST)
    sub = root / "s-abc" / "subagents"
    sub.mkdir(parents=True)
    (sub / "agent-a.jsonl").write_text(_line("s-abc", "2026-11-20T10:05:00Z", "Search the codebase", isSidechain=True),
                                       encoding="utf-8")
    (root / "agent-b.jsonl").write_text(_line("s-abc", "2026-11-20T10:05:00Z", "Old-style sidechain", isSidechain=True),
                                        encoding="utf-8")
    collect(conn, cfg, LAST + timedelta(minutes=1))
    rec = json.loads(conn.execute("SELECT record_json FROM sessions WHERE session_id='s-abc'").fetchone()[0])
    assert rec["user_prompts"][0] == "Thêm bộ lọc theo môn học cho trang đề cũ"
    assert rec["path"].endswith("s-abc.jsonl")


# I1
def test_poison_line_does_not_stall_or_lock(tmp_path):
    conn, cfg, root = _env(tmp_path)
    bad = json.dumps({"type": "assistant", "sessionId": "s-abc", "timestamp": "2026-11-20T10:04:30Z",
                      "message": {"role": "assistant", "content": [{"type": "text", "text": None}]}}) + "\n"
    with open(root / "s-abc.jsonl", "a", encoding="utf-8") as f:
        f.write(bad)
        f.write(_line("s-abc", "2026-11-20T10:04:40Z", "after the bad line"))
    collect(conn, cfg, LAST)
    assert not conn.in_transaction
    rec = json.loads(conn.execute("SELECT record_json FROM sessions WHERE session_id='s-abc'").fetchone()[0])
    assert rec["user_prompts"][-1] == "after the bad line"


def test_naive_timestamp_treated_as_utc(tmp_path):
    assert parse_ts("2026-11-20T10:00:00").tzinfo is not None
    conn, cfg, root = _env(tmp_path)
    (root / "n.jsonl").write_text(_line("n1", "2026-11-20T10:00:00", "hi"), encoding="utf-8")
    assert "n1" in collect(conn, cfg, LAST + timedelta(hours=1))


def test_file_error_rolls_back_and_continues(tmp_path, monkeypatch):
    conn, cfg, root = _env(tmp_path)
    (root / "z.jsonl").write_text(_line("z1", "2026-11-20T10:00:00Z", "z"), encoding="utf-8")
    from devradar.collector import scan
    real = scan.resolve_project

    def boom(cwd):
        if cwd == "/x/other":
            raise RuntimeError("boom")
        return real(cwd)
    monkeypatch.setattr(scan, "resolve_project", boom)
    collect(conn, cfg, LAST)
    assert not conn.in_transaction
    assert conn.execute("SELECT count(*) FROM sessions WHERE session_id='s-abc'").fetchone()[0] == 1


# I2
def test_finished_returned_in_time_order(tmp_path):
    conn, cfg, root = _env(tmp_path)
    (root / "a.jsonl").write_text(_line("zzz-old", "2026-11-20T08:00:00Z", "old"), encoding="utf-8")
    (root / "b.jsonl").write_text(_line("aaa-new", "2026-11-20T09:00:00Z", "new"), encoding="utf-8")
    assert collect(conn, cfg, LAST + timedelta(hours=1)) == ["zzz-old", "aaa-new", "s-abc"]


# I3
def _project(conn, name, last_ago, now):
    st = {"goal": "", "done": [], "next": ["deploy"], "blockers": [], "deadlines": [],
          "_next_since": (now - last_ago).isoformat()}
    conn.execute("INSERT INTO projects(name, display, state_json) VALUES(?,?,?)", (name, name, json.dumps(st)))
    t = (now - last_ago).isoformat()
    conn.execute("INSERT INTO sessions(session_id, agent, project, last_event_at, finished) VALUES(?, 'c', ?, ?, 1)",
                 (name + "-s", name, t))


def test_stalled_ignores_abandoned_projects(tmp_path):
    conn = connect(tmp_path / "d.db")
    init_schema(conn)
    cfg = load_config(tmp_path / "none.yaml")
    now = datetime(2026, 11, 27, tzinfo=UTC)
    _project(conn, "recent", timedelta(days=5), now)
    _project(conn, "abandoned", timedelta(days=40), now)
    assert [a.project for a in stalled.check(conn, now, cfg)] == ["recent"]


class CostlyLLM:
    def __init__(self, conn):
        self.conn, self.calls = conn, 0

    def complete_json(self, system, user):
        self.calls += 1
        record_usage(self.conn, 0, 0, 20_000_000, datetime(2026, 11, 20, 12, tzinfo=UTC))  # $12 in one call
        return {"goal": "", "done": [], "next": [], "blockers": [], "deadlines": []}


def test_cap_checked_inside_loop(tmp_path):
    conn, cfg, root = _env(tmp_path)
    (root / "b.jsonl").write_text(_line("s2", "2026-11-20T10:00:00Z", "b"), encoding="utf-8")
    llm = CostlyLLM(conn)
    run_cycle(conn, cfg, llm, LAST + timedelta(hours=1))
    assert llm.calls == 1


def test_old_sessions_get_no_llm_backfill(tmp_path):
    conn, cfg, root = _env(tmp_path)
    llm = CostlyLLM(conn)
    run_cycle(conn, cfg, llm, LAST + timedelta(days=30))
    assert llm.calls == 0
    assert conn.execute("SELECT excerpt FROM sessions WHERE session_id='s-abc'").fetchone()[0]


# I4
def _pending(conn):
    conn.execute("INSERT INTO alerts(rule, project, reason, created_at) VALUES('r','p','x',?)", (LAST.isoformat(),))
    conn.commit()


def test_trigger_survives_non_utf8_and_cp1252_unfriendly_output(tmp_path):
    conn, cfg, _ = _env(tmp_path)
    _pending(conn)
    cfg["hermes"]["alert_command"] = [sys.executable, "-c",
                                      "import sys; sys.stdout.buffer.write('đã gửi ọ'.encode() + b'\\xff')"]
    assert trigger_hermes(conn, cfg) is True


def test_trigger_unexpected_error_returns_false(tmp_path, monkeypatch):
    conn, cfg, _ = _env(tmp_path)
    _pending(conn)

    def bad(*a, **k):
        raise ValueError("weird")
    monkeypatch.setattr(subprocess, "run", bad)
    assert trigger_hermes(conn, cfg) is False


# I5
@pytest.fixture
def pconn(tmp_path):
    c = connect(tmp_path / "d.db")
    init_schema(c)
    c.execute("INSERT INTO projects(name, display) VALUES('p','p')")
    for sid in ("s0", "s1"):
        c.execute("INSERT INTO sessions(session_id, agent, project, finished, excerpt) VALUES(?, 'c', 'p', 1, 'e')", (sid,))
    c.commit()
    return c


class Scripted:
    def __init__(self, *r):
        self.r, self.calls = list(r), 0

    def complete_json(self, s, u):
        self.calls += 1
        return self.r.pop(0)


def _st(done):
    return {"goal": "", "done": done, "next": [], "blockers": [], "deadlines": []}


def test_citation_of_unseen_session_dropped(pconn):
    st = update_project_state(pconn, "p", "s1", Scripted(_st([{"item": "x", "session_id": "s0"}])))
    assert st.done == []


def test_non_string_done_item_is_invalid_not_crash(pconn):
    llm = Scripted(_st([{"item": ["a"], "session_id": "s1"}]), _st([{"item": "ok", "session_id": "s1"}]))
    st = update_project_state(pconn, "p", "s1", llm)
    assert llm.calls == 2 and st.done == [{"item": "ok", "session_id": "s1"}]
