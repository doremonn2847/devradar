from devradar.db import connect, init_schema

TABLES = {"files", "sessions", "projects", "facts", "alerts", "snoozes", "news_items", "usage"}


def test_init_schema_idempotent(tmp_path):
    conn = connect(tmp_path / "d.db")
    init_schema(conn)
    init_schema(conn)
    names = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert TABLES <= names
