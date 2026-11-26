"""Parser for Claude Code session JSONL (~/.claude/projects/<cwd>/<session>.jsonl)."""
from __future__ import annotations

from devradar.collector.common import add_assistant_text, add_user_prompt, load, parse_ts, touch
from devradar.models import SessionRecord


def _result_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(b.get("text", "") for b in content if isinstance(b, dict))
    return ""


def parse_claude_line(line: str, rec: SessionRecord) -> None:
    obj = load(line)
    if not obj or obj.get("type") not in ("user", "assistant"):
        return
    msg = obj.get("message")
    if not isinstance(msg, dict):
        return
    if obj.get("sessionId") and not rec.session_id:
        rec.session_id = obj["sessionId"]
    if obj.get("cwd"):
        rec.cwd = obj["cwd"]
    touch(rec, parse_ts(obj.get("timestamp")))
    content = msg.get("content")
    if obj["type"] == "user":
        if isinstance(content, str):
            add_user_prompt(rec, content)
            return
        if not isinstance(content, list):
            return
        for b in content:
            if not isinstance(b, dict):
                continue
            if b.get("type") == "text":
                add_user_prompt(rec, b.get("text", ""))
            elif b.get("type") == "tool_result" and b.get("is_error"):
                rec.errors.append(_result_text(b.get("content"))[:300])
        return
    if not isinstance(content, list):
        return
    for b in content:
        if not isinstance(b, dict):
            continue
        if b.get("type") == "text":
            add_assistant_text(rec, b.get("text", ""))
        elif b.get("type") == "tool_use":
            inp = b.get("input") if isinstance(b.get("input"), dict) else {}
            if isinstance(inp.get("file_path"), str):
                rec.files_touched.add(inp["file_path"])
            if b.get("name") == "TodoWrite" and isinstance(inp.get("todos"), list):
                rec.open_todos = [
                    t.get("content", "") for t in inp["todos"]
                    if isinstance(t, dict) and t.get("status") != "completed"
                ]
