"""Helpers shared by the session parsers."""
from __future__ import annotations

import json
from datetime import datetime, timezone

from devradar.models import SessionRecord

TAIL = 5


def load(line: str) -> dict | None:
    try:
        obj = json.loads(line)
    except (ValueError, TypeError):
        return None
    return obj if isinstance(obj, dict) else None


def parse_ts(v) -> datetime | None:
    """Parse an ISO timestamp, always returning an aware UTC datetime (naive input is taken as UTC)."""
    if not isinstance(v, str):
        return None
    try:
        t = datetime.fromisoformat(v.replace("Z", "+00:00"))
    except ValueError:
        return None
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    return t.astimezone(timezone.utc)


def touch(rec: SessionRecord, ts: datetime | None) -> None:
    if ts is None:
        return
    if rec.started_at is None or ts < rec.started_at:
        rec.started_at = ts
    if rec.last_event_at is None or ts > rec.last_event_at:
        rec.last_event_at = ts


def add_user_prompt(rec: SessionRecord, text) -> None:
    if not isinstance(text, str):
        return
    text = text.strip()
    if not text or text.startswith("<"):  # injected context, not a human prompt
        return
    rec.user_prompts.append(text)
    rec.ended_on_question = False


def add_assistant_text(rec: SessionRecord, text) -> None:
    if not isinstance(text, str):
        return
    text = text.strip()
    if not text:
        return
    rec.assistant_tail.append(text)
    del rec.assistant_tail[:-TAIL]
    rec.ended_on_question = text.endswith("?")


# ---- path helpers (session files may hold Windows paths even when read elsewhere) ----
import ntpath
import posixpath
import re as _re
from collections import Counter

_ABS = _re.compile(r"^(?:[a-zA-Z]:[\\/]|/)")
_AGY = _re.compile(r"""(?:^|[\s;&|"'/\\])agy(?:\.exe|\.cmd|\.ps1)?(?=\s|$|["'])""")


def is_abs_path(v) -> bool:
    return isinstance(v, str) and "\n" not in v and len(v) < 400 and bool(_ABS.match(v))


def _mod(p: str):
    return ntpath if "\\" in p else posixpath


def dominant_dir(paths) -> str | None:
    """Folder the paths share (their common parent), or the most frequent parent if that's too shallow."""
    paths = [p for p in paths if is_abs_path(p)]
    if not paths:
        return None
    mod = _mod(paths[0])
    same = [p for p in paths if _mod(p) is mod]
    dirs = [mod.dirname(p) for p in same]
    try:
        cp = mod.commonpath(dirs)
    except ValueError:  # different drives
        cp = ""
    parts = [x for x in _re.split(r"[\\/]+", cp) if x]
    if len(parts) >= 4:
        return cp
    return Counter(dirs).most_common(1)[0][0]


def is_agy_command(cmd) -> bool:
    if isinstance(cmd, list):
        cmd = " ".join(str(c) for c in cmd)
    return isinstance(cmd, str) and bool(_AGY.search(cmd))
