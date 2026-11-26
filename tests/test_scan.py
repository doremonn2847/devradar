import json
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

from devradar.collector.scan import collect
from devradar.config import load_config
from devradar.db import connect, init_schema

FIX = Path(__file__).parent / "fixtures"
LAST = datetime(2026, 11, 20, 10, 4, tzinfo=timezone.utc)


def _setup(tmp_path):
    cc = tmp_path / "claude" / "C--Users-me-code-de-cu"
    cx = tmp_path / "codex" / "2026" / "11" / "21"
    cc.mkdir(parents=True)
    cx.mkdir(parents=True)
    shutil.copy(FIX / "claude_session.jsonl", cc / "s-abc.jsonl")
    shutil.copy(FIX / "codex_session.jsonl", cx / "rollout-cx-1.jsonl")
    cfg = load_config(tmp_path / "none.yaml")
    cfg["collector"]["roots"] = {"claude_code": str(tmp_path / "claude"), "codex": str(tmp_path / "codex")}
    conn = connect(tmp_path / "d.db")
    init_schema(conn)
    return conn, cfg, cc / "s-abc.jsonl"


def test_collect_marks_finished(tmp_path):
    conn, cfg, _ = _setup(tmp_path)
    now = LAST + timedelta(minutes=31)
    assert collect(conn, cfg, now) == ["s-abc"]  # codex session's last event is after `now`
    assert collect(conn, cfg, now) == []
    row = conn.execute("SELECT agent, project, finished FROM sessions WHERE session_id='s-abc'").fetchone()
    assert row["agent"] == "claude_code" and row["finished"] == 1
    assert conn.execute("SELECT count(*) FROM sessions").fetchone()[0] == 2


def test_not_finished_while_recent(tmp_path):
    conn, cfg, _ = _setup(tmp_path)
    assert collect(conn, cfg, LAST + timedelta(minutes=10)) == []


def test_collect_reopens_on_new_lines(tmp_path):
    conn, cfg, f = _setup(tmp_path)
    collect(conn, cfg, LAST + timedelta(minutes=31))
    line = {"type": "user", "sessionId": "s-abc", "timestamp": "2026-11-20T12:00:00Z",
            "message": {"role": "user", "content": "deploy it"}}
    with open(f, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(line) + "\n")
    collect(conn, cfg, datetime(2026, 11, 20, 12, 5, tzinfo=timezone.utc))
    row = conn.execute("SELECT finished, record_json FROM sessions WHERE session_id='s-abc'").fetchone()
    assert row["finished"] == 0
    assert json.loads(row["record_json"])["user_prompts"][-1] == "deploy it"


def test_projects_registered_with_display(tmp_path):
    conn, cfg, _ = _setup(tmp_path)
    collect(conn, cfg, LAST)
    names = {r["display"] for r in conn.execute("SELECT display FROM projects")}
    assert {"de-cu", "docusense"} <= names
