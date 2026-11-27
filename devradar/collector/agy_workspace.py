"""Workspace (project folder) of an Antigravity conversation, from its per-conversation database.

<root>/conversations/<conversation-id>.db (SQLite) holds a protobuf blob in
trajectory_metadata_blob.data that contains the workspace as a file:/// URL. Checked on the user's
machine: present for every recent CLI and app conversation.
"""
from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from urllib.parse import unquote

_URL = re.compile(rb"file:///([A-Za-z]:/[^\x00-\x1f\"'\s]{1,300}|/[^\x00-\x1f\"'\s]{1,300})")


def workspace_from_db(db: Path) -> str | None:
    db = Path(db)
    if not db.is_file():
        return None
    try:
        con = sqlite3.connect(f"{db.resolve().as_uri()}?mode=ro", uri=True, timeout=2)
        try:
            rows = con.execute("SELECT data FROM trajectory_metadata_blob").fetchall()
        finally:
            con.close()
    except sqlite3.Error:
        return None
    for (data,) in rows:
        raw = data if isinstance(data, (bytes, bytearray)) else str(data or "").encode()
        m = _URL.search(raw)
        if m:
            return unquote(m.group(1).decode("utf-8", "replace")).rstrip("/")
    return None


def conversation_db(brain_root: Path, conversation_id: str) -> Path:
    return Path(brain_root).parent / "conversations" / f"{conversation_id}.db"
