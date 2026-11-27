import json
from datetime import datetime, timedelta, timezone

import pytest

from devradar.config import load_config
from devradar.db import connect, init_schema
from devradar.rules import evaluate_rules, normalize_error
from devradar.rules import dangling, deadline, loop, spread, stalled

NOW = datetime(2026, 11, 27, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def env(tmp_path):
    conn = connect(tmp_path / "d.db")
    init_schema(conn)
    cfg = load_config(tmp_path / "none.yaml")
    return conn, cfg


def project(conn, name, **state):
    st = {"goal": "", "done": [], "next": [], "blockers": [], "deadlines": []}
    st.update(state)
    conn.execute("INSERT OR REPLACE INTO projects(name, display, state_json) VALUES(?,?,?)",
                 (name, name, json.dumps(st)))


def session(conn, sid, proj, ago, finished=1, **rec):
    t = (NOW - ago).isoformat()
    r = {"agent": "claude_code", "session_id": sid, "path": sid, "cwd": None, "started_at": t,
         "last_event_at": t, "user_prompts": [], "assistant_tail": [], "errors": [],
         "files_touched": [], "open_todos": [], "ended_on_question": False}
    r.update(rec)
    conn.execute("INSERT INTO sessions(session_id, agent, project, path, started_at, last_event_at, finished, record_json)"
                 " VALUES(?,?,?,?,?,?,?,?)", (sid, "claude_code", proj, sid, t, t, finished, json.dumps(r)))


def test_stalled(env):
    conn, cfg = env
    project(conn, "a", next=["deploy"], _next_since=(NOW - timedelta(days=5)).isoformat())
    session(conn, "s1", "a", timedelta(days=4))
    project(conn, "b", next=["deploy"], _next_since=(NOW - timedelta(days=5)).isoformat())
    session(conn, "s2", "b", timedelta(days=2))
    assert [a.project for a in stalled.check(conn, NOW, cfg)] == ["a"]


def test_deadline(env):
    conn, cfg = env
    project(conn, "a", deadlines=[{"item": "demo", "date": (NOW + timedelta(days=10)).date().isoformat()}])
    session(conn, "s1", "a", timedelta(days=3))
    project(conn, "b", deadlines=[{"item": "demo", "date": (NOW + timedelta(days=20)).date().isoformat()}])
    session(conn, "s2", "b", timedelta(days=3))
    assert [a.project for a in deadline.check(conn, NOW, cfg)] == ["a"]


def test_normalize_error():
    a = normalize_error(r"Error: Cannot find module './x' at C:\Users\me\a.ts:42")
    b = normalize_error(r"Error: Cannot find module './x' at C:\Users\me\b.ts:97")
    assert a == b


def test_loop(env):
    conn, cfg = env
    project(conn, "a")
    for i in range(3):
        session(conn, f"s{i}", "a", timedelta(days=i + 1), errors=[f"TypeError: x is undefined (line {i * 10})"])
    project(conn, "b")
    for i in range(2):
        session(conn, f"t{i}", "b", timedelta(days=i + 1), errors=["TypeError: x is undefined"])
    assert [a.project for a in loop.check(conn, NOW, cfg)] == ["a"]


def test_dangling(env):
    conn, cfg = env
    project(conn, "a")
    session(conn, "s1", "a", timedelta(hours=25), open_todos=["deploy"])
    project(conn, "b")
    session(conn, "t1", "b", timedelta(hours=25), open_todos=["deploy"])
    session(conn, "t2", "b", timedelta(hours=2))
    assert [a.project for a in dangling.check(conn, NOW, cfg)] == ["a"]


def test_dangling_question(env):
    conn, cfg = env
    project(conn, "a")
    session(conn, "s1", "a", timedelta(hours=30), ended_on_question=True)
    assert len(dangling.check(conn, NOW, cfg)) == 1


def test_spread(env):
    conn, cfg = env
    for i in range(4):
        project(conn, f"p{i}")
        session(conn, f"s{i}", f"p{i}", timedelta(days=1))
    alerts = spread.check(conn, NOW, cfg)
    assert len(alerts) == 1


def test_spread_three_projects(env):
    conn, cfg = env
    for i in range(3):
        project(conn, f"p{i}")
        session(conn, f"s{i}", f"p{i}", timedelta(days=1))
    assert spread.check(conn, NOW, cfg) == []


def test_spread_with_done_item(env):
    conn, cfg = env
    for i in range(4):
        project(conn, f"p{i}")
        session(conn, f"s{i}", f"p{i}", timedelta(days=1))
    project(conn, "p0", done=[{"item": "x", "session_id": "s0"}])
    assert spread.check(conn, NOW, cfg) == []


def _stall_setup(conn):
    project(conn, "a", next=["deploy"], _next_since=(NOW - timedelta(days=5)).isoformat())
    session(conn, "s1", "a", timedelta(days=4))


def test_cooldown(env):
    conn, cfg = env
    _stall_setup(conn)
    assert len(evaluate_rules(conn, NOW, cfg)) == 1
    assert evaluate_rules(conn, NOW, cfg) == []
    assert len(evaluate_rules(conn, NOW + timedelta(days=4), cfg)) == 1
    assert conn.execute("SELECT count(*) FROM alerts WHERE notified=0").fetchone()[0] == 2


def test_snooze(env):
    conn, cfg = env
    _stall_setup(conn)
    conn.execute("INSERT INTO snoozes VALUES('a', ?)", ((NOW + timedelta(days=2)).isoformat(),))
    assert evaluate_rules(conn, NOW, cfg) == []
