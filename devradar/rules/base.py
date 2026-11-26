"""Shared helpers for rule modules."""
from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime


def dt(v: str | None) -> datetime | None:
    return datetime.fromisoformat(v) if v else None


def projects(conn: sqlite3.Connection) -> list[tuple[str, str, dict]]:
    out = []
    for r in conn.execute("SELECT name, display, state_json FROM projects"):
        out.append((r["name"], r["display"] or r["name"], json.loads(r["state_json"]) if r["state_json"] else {}))
    return out


def display(conn: sqlite3.Connection, project: str) -> str:
    r = conn.execute("SELECT display FROM projects WHERE name=?", (project,)).fetchone()
    return (r["display"] if r else None) or project


def last_activity(conn: sqlite3.Connection, project: str) -> datetime | None:
    r = conn.execute("SELECT max(last_event_at) AS t FROM sessions WHERE project=?", (project,)).fetchone()
    return dt(r["t"]) if r else None


def sessions_since(conn: sqlite3.Connection, since: datetime) -> list[sqlite3.Row]:
    return [r for r in conn.execute("SELECT * FROM sessions WHERE last_event_at IS NOT NULL")
            if dt(r["last_event_at"]) >= since]


def record(row: sqlite3.Row) -> dict:
    return json.loads(row["record_json"]) if row["record_json"] else {}


_WIN_PATH = re.compile(r"[a-z]:\\[^\s'\"]+")
_POSIX_PATH = re.compile(r"(?<![\w.])/[^\s'\"]+")
_HEX = re.compile(r"\b(0x)?[0-9a-f]{6,}\b")
_NUM = re.compile(r"\d+")


def normalize_error(msg: str) -> str:
    s = msg.lower()
    s = _WIN_PATH.sub("<path>", s)
    s = _POSIX_PATH.sub("<path>", s)
    s = _HEX.sub("<hex>", s)
    s = _NUM.sub("#", s)
    return " ".join(s.split())[:120]
