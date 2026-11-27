"""Fixtures derived from the user's real files (all text and paths replaced): pin the real formats."""
import json
import os
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from devradar.collector.agy_cli import parse_agy_line
from devradar.collector.agy_workspace import workspace_from_db
from devradar.collector.codex import parse_codex_line
from devradar.collector.scan import collect
from devradar.config import load_config
from devradar.db import connect, init_schema
from devradar.models import SessionRecord

FIX = Path(__file__).parent / "fixtures"
UTC = timezone.utc


def _parse(fn, name, agent):
    rec = SessionRecord(agent=agent, session_id="", path=name)
    for line in (FIX / name).read_text(encoding="utf-8").splitlines():
        fn(line, rec)
    return rec


def test_codex_item_completed_records():
    rec = _parse(parse_codex_line, "codex_real.jsonl", "codex")
    assert rec.session_id == "cx-real" and rec.cwd == r"C:\Users\me\proj\f0"
    assert rec.errors == ["lorem lorem lorem lorem lorem lorem"]          # only the exit_code 1 command
    assert rec.files_touched == {r"C:\Users\me\proj\f1.cpp", r"C:\Users\me\proj\f2.py"}
    assert rec.assistant_tail[-1] == "final lorem answer"
    assert len(rec.user_prompts) == 1                                      # two parts of one message = one prompt


def test_codex_exec_sessions_are_orchestrated():
    assert _parse(parse_codex_line, "codex_real.jsonl", "codex").orchestrated is True
    tui = SessionRecord(agent="codex", session_id="", path="p")
    parse_codex_line(json.dumps({"timestamp": "2026-11-20T17:12:27Z", "type": "session_meta",
                                 "payload": {"id": "t", "cwd": "C:\\x", "originator": "codex-tui", "source": "cli"}}), tui)
    assert tui.orchestrated is False


def test_codex_agy_launch_from_command_execution():
    rec = SessionRecord(agent="codex", session_id="s", path="p")
    parse_codex_line(json.dumps({"timestamp": "2026-11-20T17:12:44Z", "type": "event_msg", "payload": {
        "type": "item_completed", "item": {"type": "CommandExecution", "command": ["pwsh", "-c", "agy -p 'do it'"],
                                           "exit_code": 0, "aggregated_output": ""}}}), rec)
    assert len(rec.agy_launches) == 1


def test_agy_cli_real_minimal():
    rec = _parse(parse_agy_line, "agy_cli_real.jsonl", "agy_cli")
    assert rec.user_prompts == ["lorem lorem lorem lorem lorem lorem"]
    assert rec.assistant_tail == ["lorem lorem lorem lorem lorem lorem"]


def test_agy_app_real_errors_and_files():
    rec = _parse(parse_agy_line, "agy_app_real.jsonl", "agy_app")
    assert len(rec.errors) == 2                     # USER_EXPLICIT and MODEL RUN_COMMAND with exit code 1
    assert r"C:\Users\me\proj\src\a.py" in rec.files_touched
    assert rec.started_at == datetime(2026, 8, 6, 4, 17, 27, tzinfo=UTC)   # steps are not in time order
    assert rec.last_event_at == datetime(2026, 8, 6, 4, 19, 54, tzinfo=UTC)


def test_ask_question_marks_waiting_for_user():
    rec = SessionRecord(agent="agy_app", session_id="s", path="p")
    parse_agy_line(json.dumps({"step_index": 1, "source": "MODEL", "type": "PLANNER_RESPONSE", "created_at": "2026-08-06T04:19:34Z",
                               "tool_calls": [{"name": "ask_question", "args": {"questions": []}}]}), rec)
    assert rec.ended_on_question is True
    parse_agy_line(json.dumps({"step_index": 2, "source": "USER_EXPLICIT", "type": "USER_INPUT", "created_at": "2026-08-06T04:20:00Z",
                               "content": "<USER_REQUEST>\nyes\n</USER_REQUEST>"}), rec)
    assert rec.ended_on_question is False


def _db(path, ws):
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE trajectory_metadata_blob (id TEXT, data BLOB)")
    blob = b"\x0a\x12junk\x1a" + f"file:///{ws}".encode() + b"\x12\x04more"
    con.execute("INSERT INTO trajectory_metadata_blob VALUES ('x', ?)", (blob,))
    con.commit()
    con.close()


def test_workspace_from_db(tmp_path):
    _db(tmp_path / "a.db", "C:/Users/me/mvp-exam-assistant")
    assert workspace_from_db(tmp_path / "a.db") == "C:/Users/me/mvp-exam-assistant"
    _db(tmp_path / "b.db", "d:/IRS-beam%20forming")
    assert workspace_from_db(tmp_path / "b.db") == "d:/IRS-beam forming"
    assert workspace_from_db(tmp_path / "missing.db") is None
    (tmp_path / "bad.db").write_bytes(b"not sqlite")
    assert workspace_from_db(tmp_path / "bad.db") is None


def _install(tmp_path, root_name, cid, fixture):
    root = tmp_path / ".gemini" / root_name
    logs = root / "brain" / cid / ".system_generated" / "logs"
    logs.mkdir(parents=True)
    shutil.copy(FIX / fixture, logs / "transcript.jsonl")
    (root / "conversations").mkdir()
    _db(root / "conversations" / f"{cid}.db", "C:/Users/me/mvp-exam-assistant")
    return root


def test_scan_reads_app_transcripts_with_db_workspace(tmp_path):
    cli = _install(tmp_path, "antigravity-cli", "c1", "agy_cli_real.jsonl")
    app = _install(tmp_path, "antigravity", "a1", "agy_app_real.jsonl")
    cfg = load_config(tmp_path / "none.yaml")
    cfg["collector"]["roots"] = {"agy_cli": str(cli / "brain"), "agy_app": [str(app / "brain")]}
    conn = connect(tmp_path / "d.db")
    init_schema(conn)
    collect(conn, cfg, datetime(2026, 11, 24, tzinfo=UTC))
    rows = {r["session_id"]: r for r in conn.execute(
        "SELECT s.session_id, s.agent, s.record_json, p.display FROM sessions s JOIN projects p ON p.name = s.project")}
    assert rows["c1"]["agent"] == "agy_cli" and rows["a1"]["agent"] == "agy_app"
    assert rows["c1"]["display"] == rows["a1"]["display"] == "mvp-exam-assistant"
    assert json.loads(rows["c1"]["record_json"])["orchestrated"] is True
    app_rec = json.loads(rows["a1"]["record_json"])
    assert app_rec["orchestrated"] is False and len(app_rec["errors"]) == 2


def test_app_markdown_todos_overlay_transcript(tmp_path):
    app = _install(tmp_path, "antigravity", "a1", "agy_app_real.jsonl")
    (app / "brain" / "a1" / "task.md").write_text("# T\n- [x] done\n- [ ] ship it\n", encoding="utf-8")
    cfg = load_config(tmp_path / "none.yaml")
    cfg["collector"]["roots"] = {"agy_app": [str(app / "brain")]}
    conn = connect(tmp_path / "d.db")
    init_schema(conn)
    collect(conn, cfg, datetime(2026, 11, 24, tzinfo=UTC))
    rec = json.loads(conn.execute("SELECT record_json FROM sessions WHERE session_id='a1'").fetchone()[0])
    assert rec["open_todos"] == ["ship it"] and len(rec["errors"]) == 2


def test_db_workspace_beats_launch_linking(tmp_path):
    cli = _install(tmp_path, "antigravity-cli", "c1", "agy_cli_real.jsonl")
    claude = tmp_path / "claude" / "p"
    claude.mkdir(parents=True)
    launch = [{"type": "assistant", "sessionId": "s1", "cwd": "/work/orchestrator", "timestamp": "2026-11-23T04:59:00Z",
               "message": {"content": [{"type": "tool_use", "name": "Bash", "input": {"command": "agy -p x"}}]}}]
    (claude / "s1.jsonl").write_text("".join(json.dumps(x) + "\n" for x in launch), encoding="utf-8")
    cfg = load_config(tmp_path / "none.yaml")
    cfg["collector"]["roots"] = {"claude_code": str(tmp_path / "claude"), "agy_cli": str(cli / "brain")}
    conn = connect(tmp_path / "d.db")
    init_schema(conn)
    collect(conn, cfg, datetime(2026, 11, 23, 5, tzinfo=UTC))
    disp = conn.execute("SELECT p.display FROM sessions s JOIN projects p ON p.name = s.project "
                        "WHERE s.session_id='c1'").fetchone()[0]
    assert disp == "mvp-exam-assistant"


def test_claude_real_session():
    from devradar.collector.claude_code import parse_claude_line
    rec = _parse(parse_claude_line, "claude_real.jsonl", "claude_code")
    assert rec.session_id == "real-claude"
    assert rec.user_prompts == ["lorem prompt 2", "lorem prompt 1"]       # no <local-command-stdout>, no api-request-blob
    assert rec.errors == ["Exit code 1\nlorem error"]
    assert rec.files_touched == {r"C:\Users\me\proj\src\judge.py"}
    assert rec.assistant_tail == ["lorem reply 2"]
    assert rec.agy_launches == ["2026-11-15T14:27:44.520000+00:00"]        # full-path agy launch is detected
    assert rec.cwd == r"C:\Users\me\proj\tmp\tc\59"                         # scratch dir; repo.py folds it to the repo
    assert rec.last_event_at == datetime(2026, 11, 15, 14, 27, 44, 520000, tzinfo=UTC)
