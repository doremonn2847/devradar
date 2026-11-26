import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from devradar.config import load_config
from devradar.db import connect, init_schema
from devradar.usage import record_usage
from devradar.worker import run_cycle, trigger_hermes

FIX = Path(__file__).parent / "fixtures"
NOW = datetime(2026, 11, 20, 10, 40, tzinfo=timezone.utc)  # 36 min after the fixture's last event


class FakeLLM:
    def __init__(self):
        self.calls = 0

    def complete_json(self, system, user):
        self.calls += 1
        return {"goal": "g", "done": [], "next": ["deploy"], "blockers": [], "deadlines": []}


@pytest.fixture
def env(tmp_path):
    root = tmp_path / "claude" / "proj"
    root.mkdir(parents=True)
    shutil.copy(FIX / "claude_session.jsonl", root / "s-abc.jsonl")
    cfg = load_config(tmp_path / "none.yaml")
    cfg["collector"]["roots"] = {"claude_code": str(tmp_path / "claude")}
    conn = connect(tmp_path / "d.db")
    init_schema(conn)
    return conn, cfg


def test_cycle_stores_excerpt_and_updates_state(env):
    conn, cfg = env
    llm = FakeLLM()
    run_cycle(conn, cfg, llm, NOW)
    assert llm.calls == 1
    ex = conn.execute("SELECT excerpt FROM sessions WHERE session_id='s-abc'").fetchone()[0]
    assert "Deploy to dodangchien.id.vn" in ex
    assert conn.execute("SELECT state_json FROM projects").fetchone()[0] is not None


def test_cycle_skips_llm_over_cap(env):
    conn, cfg = env
    record_usage(conn, 0, 0, 12_000_000, NOW - timedelta(days=1))  # $7.20 off-peak output
    llm = FakeLLM()
    run_cycle(conn, cfg, llm, NOW)
    assert llm.calls == 0
    assert conn.execute("SELECT excerpt FROM sessions").fetchone()[0]


def test_cycle_without_llm(env):
    conn, cfg = env
    run_cycle(conn, cfg, None, NOW)
    assert conn.execute("SELECT excerpt FROM sessions").fetchone()[0]


def _alert(conn):
    conn.execute("INSERT INTO alerts(rule, project, reason, created_at) VALUES('stalled','p','r',?)", (NOW.isoformat(),))
    conn.commit()


def test_trigger_marks_notified(env):
    conn, cfg = env
    _alert(conn)
    cfg["hermes"]["alert_command"] = [sys.executable, "-c", "pass"]
    assert trigger_hermes(conn, cfg) is True
    assert conn.execute("SELECT notified FROM alerts").fetchone()[0] == 1


def test_trigger_failure_keeps_pending(env):
    conn, cfg = env
    _alert(conn)
    cfg["hermes"]["alert_command"] = [sys.executable, "-c", "raise SystemExit(1)"]
    assert trigger_hermes(conn, cfg) is False
    assert conn.execute("SELECT notified FROM alerts").fetchone()[0] == 0


def test_trigger_missing_binary(env):
    conn, cfg = env
    _alert(conn)
    cfg["hermes"]["alert_command"] = ["definitely-not-a-real-binary-xyz"]
    assert trigger_hermes(conn, cfg) is False


def test_no_alerts_no_trigger(env, monkeypatch):
    conn, cfg = env
    called = []
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: called.append(a))
    assert trigger_hermes(conn, cfg) is False
    assert called == []
