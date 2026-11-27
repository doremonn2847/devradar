"""Reader for Antigravity app/editor conversations.

~/.gemini/{antigravity,antigravity-ide,antigravity-backup}/brain/<conversation-id>/*.md
The app keeps Markdown artifacts (task.md, implementation_plan.md, walkthrough.md, ...) but no
message log, so a conversation folder is re-read whole whenever any file in it changes.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote

from devradar.collector.common import dominant_dir
from devradar.models import SessionRecord

_OPEN_ITEM = re.compile(r"^\s*[-*]\s*\[( |/)\]\s*(.+?)\s*$", re.M)
_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_FILE_URL = re.compile(r"file:///([^\s)\"'>]+)")
TEXT_FILES = ("walkthrough.md", "implementation_plan.md", "proposal.md")


def signature(d: Path) -> tuple[int, int]:
    """(newest mtime in ns, total size): changes whenever any file in the folder changes."""
    newest, total = 0, 0
    for f in d.iterdir():
        if f.is_file():
            st = f.stat()
            newest, total = max(newest, st.st_mtime_ns), total + st.st_size
    return newest, total


def _read(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def read_task_todos(d: Path) -> list[str]:
    return [_LINK.sub(r"\1", m.group(2)) for m in _OPEN_ITEM.finditer(_read(d / "task.md"))]


def read_agy_app_dir(d: Path) -> SessionRecord:
    rec = SessionRecord(agent="agy_app", session_id=d.name, path=str(d))
    mtimes, texts = [], {}
    for f in sorted(d.iterdir()):
        if f.is_file() and f.suffix == ".md":
            mtimes.append(f.stat().st_mtime)
            texts[f.name] = _read(f)
    if mtimes:
        rec.started_at = datetime.fromtimestamp(min(mtimes), timezone.utc)
        rec.last_event_at = datetime.fromtimestamp(max(mtimes), timezone.utc)
    task = texts.get("task.md", "")
    rec.open_todos = [_LINK.sub(r"\1", m.group(2)) for m in _OPEN_ITEM.finditer(task)]
    first = next((ln.lstrip("# ").strip() for ln in task.splitlines() if ln.startswith("#")), "")
    if first:
        rec.user_prompts.append(first)  # the task title is the closest thing to the user's ask
    for name in TEXT_FILES:
        if texts.get(name, "").strip():
            rec.assistant_tail.append(f"[{name}] " + texts[name].strip()[:1500])
    paths = [unquote(m) for t in texts.values() for m in _FILE_URL.findall(t)]
    rec.files_touched = set(paths)
    rec.cwd = dominant_dir(paths)
    rec.cwd_source = "inferred" if rec.cwd else ""
    return rec
