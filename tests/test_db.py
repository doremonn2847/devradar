from devradar.db import connect, init_schema

TABLES = {"files", "sessions", "projects", "facts", "alerts", "snoozes", "news_items", "usage"}


def test_init_schema_idempotent(tmp_path):
    conn = connect(tmp_path / "d.db")
    init_schema(conn)
    init_schema(conn)
    names = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert TABLES <= names


def test_connection_usable_from_scheduler_thread(tmp_path):
    import threading
    conn = connect(tmp_path / "d.db")
    init_schema(conn)
    errors = []

    def job():
        try:
            conn.execute("SELECT count(*) FROM alerts").fetchone()
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    t = threading.Thread(target=job)
    t.start()
    t.join()
    assert errors == []
