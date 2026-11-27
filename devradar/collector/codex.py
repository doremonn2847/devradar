"""Parser for Codex rollout JSONL (~/.codex/sessions/**/rollout-*.jsonl)."""
from __future__ import annotations

import json
import re

from devradar.collector.common import (add_assistant_text, add_user_prompt, is_agy_command, load, parse_ts,
                                       touch)
from devradar.models import SessionRecord

_PATCH_FILE = re.compile(r"^\*\*\* (?:Add|Update|Delete) File: (.+?)\s*$", re.M)


def _patch_files(text, rec: SessionRecord) -> None:
    if isinstance(text, str):
        rec.files_touched.update(_PATCH_FILE.findall(text))


def _tool_call(payload: dict, rec: SessionRecord, ts) -> None:
    if payload.get("type") == "custom_tool_call":
        if payload.get("name") == "apply_patch":
            _patch_files(payload.get("input"), rec)
        return
    try:
        args = json.loads(payload.get("arguments") or "{}")
    except (ValueError, TypeError):
        return
    cmd = args.get("command") if isinstance(args, dict) else None
    if isinstance(cmd, list):
        if cmd and cmd[0] == "apply_patch":
            _patch_files(" ".join(str(c) for c in cmd[1:]), rec)
        elif any("apply_patch" in str(c) for c in cmd):  # e.g. ["bash", "-lc", "apply_patch <<'EOF' ..."]
            _patch_files("\n".join(str(c) for c in cmd), rec)
    if is_agy_command(cmd) and ts:
        rec.agy_launches.append(ts.isoformat())


def _tool_output(payload: dict, rec: SessionRecord) -> None:
    raw = payload.get("output")
    try:
        out = json.loads(raw) if isinstance(raw, str) else raw
    except ValueError:
        return
    if not isinstance(out, dict):
        return
    code = (out.get("metadata") or {}).get("exit_code")
    if isinstance(code, int) and code != 0:
        rec.errors.append(str(out.get("output") or f"exit code {code}")[:300])


def parse_codex_line(line: str, rec: SessionRecord) -> None:
    obj = load(line)
    if not obj:
        return
    payload = obj.get("payload")
    if not isinstance(payload, dict):
        return
    kind = obj.get("type")
    ts = parse_ts(obj.get("timestamp"))
    if kind == "session_meta":
        if payload.get("id") and not rec.session_id:
            rec.session_id = payload["id"]
        if payload.get("cwd"):
            rec.cwd = payload["cwd"]
        touch(rec, ts)
        return
    if kind != "response_item":
        return
    ptype = payload.get("type")
    touch(rec, ts)
    if ptype in ("function_call", "custom_tool_call"):
        _tool_call(payload, rec, ts)
        return
    if ptype == "function_call_output":
        _tool_output(payload, rec)
        return
    if ptype != "message":
        return
    parts = payload.get("content") if isinstance(payload.get("content"), list) else []
    text = " ".join(p.get("text", "") for p in parts if isinstance(p, dict) and isinstance(p.get("text"), str))
    if payload.get("role") == "user":
        add_user_prompt(rec, text)
    elif payload.get("role") == "assistant":
        add_assistant_text(rec, text)
