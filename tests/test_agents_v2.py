"""Codex tool records, Antigravity CLI transcripts and Antigravity app artifacts."""
import json
import os
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from devradar.collector.agy_app import read_agy_app_dir
from devradar.collector.agy_cli import parse_agy_line
from devradar.collector.codex import parse_codex_line
from devradar.collector.scan import collect
from devradar.config import load_config
from devradar.db import connect, init_schema
from devradar.models import SessionRecord
from devradar.rules import dangling

FIX = Path(__file__).parent / "fixtures"
UTC = timezone.utc


def _parse(fn, name, agent):
    rec = SessionRecord(agent=agent, session_id="", path=name)
    for line in (FIX / name).read_text(encoding="utf-8").splitlines():
        fn(line, rec)
    return rec


# ---- Codex ----
def test_codex_errors_and_files():
    rec = _parse(parse_codex_line, "codex_tools.jsonl", "codex")
    assert rec.errors == ["ModuleNotFoundError: No module named 'cv2'"]
    assert rec.files_touched == {"src/ocr.py", "tests/test_ocr.py"}
    assert rec.assistant_tail == ["Added test; cv2 missing."]


# ---- Antigravity CLI ----
def test_agy_cli_parse():
    rec = _parse(parse_agy_line, "agy_transcript.jsonl", "agy_cli")
    assert rec.user_prompts == ["Refactor the OCR pipeline into modules"]
    assert rec.assistant_tail[-1] == "Done: pipeline split into modules."
    assert rec.orchestrated is True
    assert rec.errors and "ImportError" in rec.errors[0]
    assert r"C:\Users\me\code\docusense\src\pipeline\__init__.py" in rec.files_touched
    assert rec.cwd == r"C:\Users\me\code\docusense"   # Cwd arg wins over file-path inference
    assert rec.started_at == datetime(2026, 11, 22, 10, 0, tzinfo=UTC)
    assert rec.ended_on_question is False


def test_agy_cli_cwd_inferred_from_paths():
    rec = SessionRecord(agent="agy_cli", session_id="x", path="p")
    for i, p in enumerate([r"C:\Users\me\code\de-cu\src\a.ts", r"C:\Users\me\code\de-cu\src\lib\b.ts",
                           r"C:\Users\me\code\de-cu\README.md"]):
        parse_agy_line(json.dumps({"step_index": i, "source": "MODEL", "type": "CODE_ACTION",
                                   "created_at": "2026-11-22T10:00:00Z",
                                   "tool_calls": [{"name": "view_file", "args": {"AbsolutePath": p}}]}), rec)
    assert rec.cwd == r"C:\Users\me\code\de-cu"


def _claude_launch(sid, cwd, ts, cmd="agy -p 'refactor ocr'"):
    lines = [
        {"type": "user", "sessionId": sid, "cwd": cwd, "timestamp": ts, "message": {"role": "user", "content": "delegate it"}},
        {"type": "assistant", "sessionId": sid, "cwd": cwd, "timestamp": ts,
         "message": {"role": "assistant", "content": [{"type": "tool_use", "id": "b1", "name": "Bash", "input": {"command": cmd}}]}},
    ]
    return "".join(json.dumps(x) + "\n" for x in lines)


def _env(tmp_path):
    cfg = load_config(tmp_path / "none.yaml")
    claude = tmp_path / "claude" / "p"
    brain = tmp_path / "gemini" / "antigravity-cli" / "brain"
    claude.mkdir(parents=True)
    brain.mkdir(parents=True)
    cfg["collector"]["roots"] = {"claude_code": str(tmp_path / "claude"), "agy_cli": str(brain),
                                 "agy_app": [str(tmp_path / "gemini" / n / "brain") for n in ("antigravity", "antigravity-ide")]}
    conn = connect(tmp_path / "d.db")
    init_schema(conn)
    return conn, cfg, claude, brain


def _agy_conv(brain, cid):
    logs = brain / cid / ".system_generated" / "logs"
    logs.mkdir(parents=True)
    shutil.copy(FIX / "agy_transcript.jsonl", logs / "transcript.jsonl")
    (logs / "transcript_full.jsonl").write_text("{}\n", encoding="utf-8")


def test_agy_links_to_launching_claude_session(tmp_path):
    conn, cfg, claude, brain = _env(tmp_path)
    (claude / "s1.jsonl").write_text(_claude_launch("s1", "/work/orchestrator-repo", "2026-11-22T09:59:40Z"), encoding="utf-8")
    (claude / "s2.jsonl").write_text(_claude_launch("s2", "/work/other", "2026-11-22T06:00:00Z"), encoding="utf-8")
    _agy_conv(brain, "conv-1")
    collect(conn, cfg, datetime(2026, 11, 22, 11, tzinfo=UTC))
    rows = {r["session_id"]: r for r in conn.execute("SELECT session_id, agent, project FROM sessions")}
    assert set(rows) == {"s1", "s2", "conv-1"}       # transcript_full.jsonl is not a second session
    assert rows["conv-1"]["agent"] == "agy_cli"
    assert rows["conv-1"]["project"] == rows["s1"]["project"]


def test_agy_without_parent_uses_inferred_cwd(tmp_path):
    conn, cfg, claude, brain = _env(tmp_path)
    _agy_conv(brain, "conv-1")
    collect(conn, cfg, datetime(2026, 11, 22, 11, tzinfo=UTC))
    disp = conn.execute("SELECT p.display FROM sessions s JOIN projects p ON p.name = s.project").fetchone()[0]
    assert disp == "docusense"


def test_agy_launch_detection_ignores_lookalikes():
    from devradar.collector.claude_code import parse_claude_line
    rec = SessionRecord(agent="claude_code", session_id="s", path="p")
    for cmd in ("cat legacy.txt", "agy --help", "npx agy run", "C:\\tools\\agy.exe -p x"):
        line = json.dumps({"type": "assistant", "sessionId": "s", "timestamp": "2026-11-22T10:00:00Z",
                           "message": {"content": [{"type": "tool_use", "name": "Bash", "input": {"command": cmd}}]}})
        parse_claude_line(line, rec)
    assert len(rec.agy_launches) == 3


# ---- Antigravity app ----
TASK = """# Task: Exam filter
- [x] Build subject filter
- [/] Wire filter to API
- [ ] Deploy to [site](file:///c:/Users/me/code/mvp-exam-assistant/deploy.md)
"""
WALK = "# Walkthrough\nAdded the filter in [Filter.tsx](file:///c:/Users/me/code/mvp-exam-assistant/src/Filter.tsx).\n"


def _app_conv(root, cid, task=TASK, walk=WALK, mtime=None):
    d = root / cid
    d.mkdir(parents=True, exist_ok=True)
    (d / "task.md").write_text(task, encoding="utf-8")
    (d / "walkthrough.md").write_text(walk, encoding="utf-8")
    (d / "task.md.metadata.json").write_text("{}", encoding="utf-8")
    if mtime:
        for f in d.iterdir():
            os.utime(f, (mtime, mtime))
    return d


def test_agy_app_reader(tmp_path):
    d = _app_conv(tmp_path, "conv-9")
    rec = read_agy_app_dir(d)
    assert rec.session_id == "conv-9" and rec.agent == "agy_app"
    assert rec.open_todos == ["Wire filter to API", "Deploy to site"]
    assert any("Added the filter" in t for t in rec.assistant_tail)
    assert rec.cwd == "c:/Users/me/code/mvp-exam-assistant"
    assert rec.orchestrated is False and rec.last_event_at is not None


def test_agy_app_newest_copy_wins_and_rereads(tmp_path):
    conn, cfg, _, _ = _env(tmp_path)
    old_root = tmp_path / "gemini" / "antigravity" / "brain"
    new_root = tmp_path / "gemini" / "antigravity-ide" / "brain"
    t0 = datetime(2026, 11, 22, 8, tzinfo=UTC).timestamp()
    _app_conv(old_root, "conv-9", task="- [ ] stale todo\n", mtime=t0)
    newd = _app_conv(new_root, "conv-9", mtime=t0 + 3600)
    collect(conn, cfg, datetime(2026, 11, 22, 12, tzinfo=UTC))
    rec = json.loads(conn.execute("SELECT record_json FROM sessions WHERE session_id='conv-9'").fetchone()[0])
    assert rec["open_todos"] == ["Wire filter to API", "Deploy to site"]
    (newd / "task.md").write_text("- [x] all done\n", encoding="utf-8")
    os.utime(newd / "task.md", (t0 + 7200, t0 + 7200))
    collect(conn, cfg, datetime(2026, 11, 22, 12, 5, tzinfo=UTC))
    rec = json.loads(conn.execute("SELECT record_json FROM sessions WHERE session_id='conv-9'").fetchone()[0])
    assert rec["open_todos"] == []


# ---- rules / worker treat orchestrated sessions as sub-work ----
def test_dangling_skips_orchestrated(tmp_path):
    conn = connect(tmp_path / "d.db")
    init_schema(conn)
    now = datetime(2026, 11, 27, tzinfo=UTC)
    t = (now - timedelta(hours=30)).isoformat()
    rec = {"agent": "agy_cli", "session_id": "a", "path": "a", "open_todos": ["x"], "ended_on_question": True,
           "orchestrated": True, "last_event_at": t}
    conn.execute("INSERT INTO projects(name, display) VALUES('p','p')")
    conn.execute("INSERT INTO sessions(session_id, agent, project, last_event_at, finished, record_json) VALUES('a','agy_cli','p',?,1,?)",
                 (t, json.dumps(rec)))
    assert dangling.check(conn, now, load_config(tmp_path / "none.yaml")) == []


def test_worker_no_llm_for_orchestrated(tmp_path):
    from devradar.worker import run_cycle
    conn, cfg, claude, brain = _env(tmp_path)
    _agy_conv(brain, "conv-1")

    class Count:
        calls = 0

        def complete_json(self, s, u):
            Count.calls += 1
            return {"goal": "", "done": [], "next": [], "blockers": [], "deadlines": []}
    run_cycle(conn, cfg, Count(), datetime(2026, 11, 22, 11, tzinfo=UTC))
    assert Count.calls == 0
    assert conn.execute("SELECT excerpt FROM sessions WHERE session_id='conv-1'").fetchone()[0]
