"""Parser for Antigravity CLI (`agy`) transcripts.

~/.gemini/antigravity-cli/brain/<conversation-id>/.system_generated/logs/transcript.jsonl
One JSON record per step: step_index, source (USER_EXPLICIT|MODEL|SYSTEM), type, status,
created_at, content, tool_calls[{name, args}]. The transcript has no working-directory field,
so the project comes from the conversation's database (agy_workspace), else the launching
session (scan.link_orchestrated), else from paths. Used for both the CLI and the app.
"""
from __future__ import annotations

import re

from devradar.collector.common import (add_assistant_text, add_user_prompt, dominant_dir, is_abs_path, load,
                                       parse_ts, touch)
from devradar.models import SessionRecord

_TAGS = re.compile(r"</?USER_REQUEST>")
_EXIT = re.compile(r"exit code[:\s]+(-?\d+)", re.I)
CWD_KEYS = {"Cwd", "cwd", "workdir", "WorkingDirectory"}
SKIP_KEYS = {"CommandLine", "command", "Command", "content", "CodeContent", "Query"}


def _is_error(obj: dict) -> bool:
    if str(obj.get("status", "")).upper() in ("ERROR", "FAILED"):
        return True
    m = _EXIT.search(obj.get("content") or "") if isinstance(obj.get("content"), str) else None
    return bool(m and int(m.group(1)) != 0)


def parse_agy_line(line: str, rec: SessionRecord) -> None:
    obj = load(line)
    if not obj or "step_index" not in obj:
        return
    touch(rec, parse_ts(obj.get("created_at")))
    source, kind, content = obj.get("source"), obj.get("type"), obj.get("content")
    if source == "USER_EXPLICIT" and kind == "USER_INPUT" and isinstance(content, str):
        add_user_prompt(rec, _TAGS.sub("", content))
    elif source == "MODEL" and kind == "PLANNER_RESPONSE":
        add_assistant_text(rec, content)
    if kind == "RUN_COMMAND" and _is_error(obj):
        rec.errors.append(str(content)[:300])
    for call in obj.get("tool_calls") or []:
        if isinstance(call, dict) and call.get("name") == "ask_question":
            rec.ended_on_question = True  # the agent is waiting on the user
        args = call.get("args") if isinstance(call, dict) else None
        if not isinstance(args, dict):
            continue
        for k, v in args.items():
            if k in CWD_KEYS and is_abs_path(v):
                if rec.cwd_source != "workspace":
                    rec.cwd, rec.cwd_source = v, "explicit"
            elif k not in SKIP_KEYS and is_abs_path(v):
                rec.files_touched.add(v)
    if rec.cwd_source not in ("explicit", "workspace"):
        inferred = dominant_dir(rec.files_touched)
        if inferred:
            rec.cwd, rec.cwd_source = inferred, "inferred"
