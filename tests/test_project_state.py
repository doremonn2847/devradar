import json

import pytest

from devradar.db import connect, init_schema
from devradar.state.project_state import update_project_state


class FakeLLM:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def complete_json(self, system, user):
        self.calls.append(json.loads(user))
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "d.db")
    init_schema(c)
    c.execute("INSERT INTO projects(name, display) VALUES('p', 'proj')")
    for sid in ("s0", "s1"):
        c.execute("INSERT INTO sessions(session_id, agent, project, finished, excerpt) VALUES(?, 'claude_code', 'p', 1, ?)",
                  (sid, f"excerpt of {sid}"))
    c.commit()
    return c


def _state(**kw):
    base = {"goal": "ship filter", "done": [], "next": ["deploy"], "blockers": [], "deadlines": []}
    base.update(kw)
    return base


def _stored(conn):
    return json.loads(conn.execute("SELECT state_json FROM projects WHERE name='p'").fetchone()[0])


def test_state_update_happy(conn):
    llm = FakeLLM(_state(done=[{"item": "filter UI", "session_id": "s1"}]))
    st = update_project_state(conn, "p", "s1", llm)
    assert st.done == [{"item": "filter UI", "session_id": "s1"}]
    assert _stored(conn)["done"][0]["session_id"] == "s1"
    assert llm.calls[0]["excerpt"] == "excerpt of s1"
    assert llm.calls[0]["session_id"] == "s1"


def test_foreign_citation_dropped(conn):
    llm = FakeLLM(_state(done=[{"item": "x", "session_id": "zzz"}, {"item": "y"}]))
    st = update_project_state(conn, "p", "s1", llm)
    assert st.done == []


def test_prior_done_kept(conn):
    update_project_state(conn, "p", "s0", FakeLLM(_state(done=[{"item": "a", "session_id": "s0"}])))
    st = update_project_state(conn, "p", "s1", FakeLLM(_state(done=[{"item": "b", "session_id": "s1"}])))
    assert [d["item"] for d in st.done] == ["a", "b"]


def test_bad_json_retry_then_keep(conn):
    update_project_state(conn, "p", "s0", FakeLLM(_state(goal="old")))
    llm = FakeLLM(ValueError("not json"), ValueError("not json"))
    assert update_project_state(conn, "p", "s1", llm) is None
    assert len(llm.calls) == 2
    assert _stored(conn)["goal"] == "old"


def test_invalid_shape_retried(conn):
    llm = FakeLLM({"goal": 5}, _state(goal="fine"))
    st = update_project_state(conn, "p", "s1", llm)
    assert st.goal == "fine" and len(llm.calls) == 2


def test_pinned_fact_wins(conn):
    conn.execute("INSERT INTO facts VALUES('p', 'deadline', '2026-12-20')")
    llm = FakeLLM(_state(deadlines=[{"item": "demo", "date": "2027-01-01"}]))
    st = update_project_state(conn, "p", "s1", llm)
    assert [d["date"] for d in st.deadlines] == ["2026-12-20"]
    assert llm.calls[0]["pinned_facts"] == {"deadline": "2026-12-20"}
