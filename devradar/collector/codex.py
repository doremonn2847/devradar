"""Parser for Codex rollout JSONL (~/.codex/sessions/**/rollout-*.jsonl)."""
from __future__ import annotations

from devradar.collector.common import add_assistant_text, add_user_prompt, load, parse_ts, touch
from devradar.models import SessionRecord


def parse_codex_line(line: str, rec: SessionRecord) -> None:
    obj = load(line)
    if not obj:
        return
    payload = obj.get("payload")
    if not isinstance(payload, dict):
        return
    kind = obj.get("type")
    if kind == "session_meta":
        if payload.get("id") and not rec.session_id:
            rec.session_id = payload["id"]
        if payload.get("cwd"):
            rec.cwd = payload["cwd"]
        touch(rec, parse_ts(obj.get("timestamp")))
        return
    if kind != "response_item" or payload.get("type") != "message":
        return
    touch(rec, parse_ts(obj.get("timestamp")))
    parts = payload.get("content") if isinstance(payload.get("content"), list) else []
    text = " ".join(p.get("text", "") for p in parts if isinstance(p, dict))
    if payload.get("role") == "user":
        add_user_prompt(rec, text)
    elif payload.get("role") == "assistant":
        add_assistant_text(rec, text)
