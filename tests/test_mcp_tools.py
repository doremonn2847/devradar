import json
from datetime import datetime, timedelta, timezone

import pytest

from devradar.config import load_config
from devradar.db import connect, init_schema
from devradar.mcp_server import Tools

NOW = datetime(2026, 11, 27, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def tools(tmp_path):
    conn = connect(tmp_path / "d.db")
    init_schema(conn)
    st = {"goal": "past papers", "done": [], "next": ["deploy"], "blockers": [], "deadlines": []}
    conn.execute("INSERT INTO projects(name, display, state_json) VALUES('c:\\code\\de-cu', 'Đề Cũ', ?)", (json.dumps(st),))
    rec = {"user_prompts": ["Thêm bộ lọc " + "x" * 300]}
    conn.execute("INSERT INTO sessions(session_id, agent, project, started_at, last_event_at, finished, record_json) "
                 "VALUES('s1', 'claude_code', 'c:\\code\\de-cu', ?, ?, 1, ?)",
                 (NOW.isoformat(), NOW.isoformat(), json.dumps(rec)))
    conn.execute("INSERT INTO alerts(rule, project, reason, created_at) VALUES('stalled', 'c:\\code\\de-cu', 'quiet', ?)",
                 (NOW.isoformat(),))
    conn.execute("INSERT INTO news_items(source, title, url, kind, score, fetched_at) VALUES('hn', 'T', 'u', 'eng', 2, ?)",
                 (NOW.isoformat(),))
    conn.commit()
    return Tools(conn, load_config(tmp_path / "none.yaml"), clock=lambda: NOW)


def test_get_project_by_display_casefold(tools):
    p = tools.get_project("đề cũ")
    assert p["display"] == "Đề Cũ"
    assert p["state"]["next"] == ["deploy"]
    assert p["open_alerts"][0]["reason"] == "quiet"


def test_unknown_project(tools):
    assert "error" in tools.get_project("nope")


def test_list_projects(tools):
    assert tools.list_projects() == [{"name": "c:\\code\\de-cu", "display": "Đề Cũ",
                                      "last_activity": NOW.isoformat(), "next": "deploy"}]


def test_history_truncates_and_hides_record(tools):
    h = tools.history("Đề Cũ", 7)
    assert h[0]["session_id"] == "s1" and len(h[0]["first_prompt"]) <= 200
    assert "record_json" not in h[0]


def test_snooze_writes(tools):
    tools.snooze("Đề Cũ", 2)
    until = tools.conn.execute("SELECT until FROM snoozes").fetchone()[0]
    assert datetime.fromisoformat(until) == NOW + timedelta(days=2)


def test_set_fact_rejects_unknown_key(tools):
    assert tools.set_fact("Đề Cũ", "color", "red").startswith("error")
    assert tools.conn.execute("SELECT count(*) FROM facts").fetchone()[0] == 0
    tools.set_fact("Đề Cũ", "deadline", "2026-12-20")
    assert tools.conn.execute("SELECT value FROM facts WHERE key='deadline'").fetchone()[0] == "2026-12-20"


def test_set_fact_rejects_bad_date(tools):
    assert tools.set_fact("Đề Cũ", "deadline", "next friday").startswith("error")


def test_ack_alert(tools):
    aid = tools.get_alerts()[0]["id"]
    assert tools.ack_alert(aid) is True
    assert tools.get_alerts() == []


def test_news_feedback(tools):
    nid = tools.news_candidates()[0]["id"]
    assert not tools.news_feedback(nid, "meh").startswith("ok")
    tools.news_feedback(nid, "less")
    assert tools.news_detail(nid)["feedback"] == "less"


def test_usage_month(tools):
    assert tools.usage_month() == {"cost_usd": 0, "cap_usd": 6}


def test_server_registers_all_tools(tools):
    import asyncio
    from devradar.mcp_server import TOOL_NAMES, build_server
    listed = asyncio.run(build_server(tools).list_tools())
    assert {t.name for t in listed} == set(TOOL_NAMES)
