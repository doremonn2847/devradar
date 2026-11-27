import json
from pathlib import Path

from devradar.collector.claude_code import parse_claude_line
from devradar.collector.codex import parse_codex_line
from devradar.models import SessionRecord

FIX = Path(__file__).parent / "fixtures"


def _parse(fn, name, agent):
    rec = SessionRecord(agent=agent, session_id="", path=str(FIX / name))
    for line in (FIX / name).read_text(encoding="utf-8").splitlines():
        fn(line, rec)
    return rec


def test_claude_fixture():
    rec = _parse(parse_claude_line, "claude_session.jsonl", "claude_code")
    assert rec.session_id == "s-abc"
    assert rec.cwd == r"C:\Users\me\code\de-cu"
    assert rec.user_prompts[0] == "Thêm bộ lọc theo môn học cho trang đề cũ"
    assert rec.user_prompts == ["Thêm bộ lọc theo môn học cho trang đề cũ", "ok, commit it"]
    assert rec.open_todos == ["Deploy to dodangchien.id.vn"]
    assert rec.errors and rec.errors[0].startswith("Error: Cannot find module")
    assert r"C:\Users\me\code\de-cu\src\filter.ts" in rec.files_touched
    assert rec.assistant_tail[-1] == "Committed. Should I deploy now?"
    assert rec.started_at.isoformat().startswith("2026-11-20T10:00:00")
    assert rec.last_event_at.isoformat().startswith("2026-11-20T10:04:00")
    assert rec.ended_on_question is True


def test_codex_fixture():
    rec = _parse(parse_codex_line, "codex_session.jsonl", "codex")
    assert rec.session_id == "cx-1"
    assert rec.cwd == r"C:\Users\me\code\docusense"
    assert rec.user_prompts == ["Fix the OCR pipeline build"]
    assert rec.assistant_tail == ["Build fixed; tests pass."]


def test_malformed_line_ignored():
    rec = SessionRecord(agent="claude_code", session_id="x", path="p")
    before = rec.to_json_dict()
    parse_claude_line("not json", rec)
    parse_claude_line('{"type": "user", "message": 5}', rec)
    assert rec.to_json_dict() == before


def _line(kind, text, ts):
    content = text if kind == "user" else [{"type": "text", "text": text}]
    return json.dumps({"type": kind, "sessionId": "q", "timestamp": ts,
                       "message": {"role": kind, "content": content}})


def test_ended_on_question():
    rec = SessionRecord(agent="claude_code", session_id="q", path="p")
    parse_claude_line(_line("user", "go", "2026-11-20T10:00:00Z"), rec)
    parse_claude_line(_line("assistant", "Should I deploy now?", "2026-11-20T10:01:00Z"), rec)
    assert rec.ended_on_question is True
    parse_claude_line(_line("user", "yes", "2026-11-20T10:02:00Z"), rec)
    assert rec.ended_on_question is False


def test_assistant_tail_keeps_last_five():
    rec = SessionRecord(agent="claude_code", session_id="q", path="p")
    for i in range(8):
        parse_claude_line(_line("assistant", f"a{i}", "2026-11-20T10:00:00Z"), rec)
    assert rec.assistant_tail == ["a3", "a4", "a5", "a6", "a7"]
