"""SQLite connection and schema."""
from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
  path TEXT PRIMARY KEY, offset INTEGER NOT NULL DEFAULT 0, size INTEGER NOT NULL DEFAULT 0, agent TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
  session_id TEXT PRIMARY KEY, agent TEXT NOT NULL, project TEXT, path TEXT,
  started_at TEXT, last_event_at TEXT, finished INTEGER NOT NULL DEFAULT 0,
  record_json TEXT, excerpt TEXT
);
CREATE INDEX IF NOT EXISTS idx_sessions_project ON sessions(project, last_event_at);
CREATE TABLE IF NOT EXISTS projects (
  name TEXT PRIMARY KEY, display TEXT, state_json TEXT, updated_at TEXT
);
CREATE TABLE IF NOT EXISTS facts (
  project TEXT NOT NULL, key TEXT NOT NULL, value TEXT, PRIMARY KEY (project, key)
);
CREATE TABLE IF NOT EXISTS alerts (
  id INTEGER PRIMARY KEY AUTOINCREMENT, rule TEXT NOT NULL, project TEXT NOT NULL, reason TEXT,
  created_at TEXT NOT NULL, notified INTEGER NOT NULL DEFAULT 0, acked INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS snoozes (project TEXT PRIMARY KEY, until TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS news_items (
  id INTEGER PRIMARY KEY AUTOINCREMENT, source TEXT, title TEXT, url TEXT UNIQUE, summary TEXT,
  published_at TEXT, kind TEXT, score REAL, fetched_at TEXT, feedback TEXT
);
CREATE TABLE IF NOT EXISTS usage (
  id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, hit INTEGER, miss INTEGER, output INTEGER, cost_usd REAL
);
"""


def connect(path: Path) -> sqlite3.Connection:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # The worker's scheduler runs jobs on a single pool thread, not the thread that opened the db.
    conn = sqlite3.connect(path, timeout=10, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()
