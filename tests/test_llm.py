import json
from types import SimpleNamespace

import pytest

from devradar.config import load_config
from devradar.db import connect, init_schema


class FakeCompletions:
    def __init__(self):
        self.kwargs = None

    def create(self, **kwargs):
        self.kwargs = kwargs
        usage = SimpleNamespace(prompt_tokens=100, prompt_cache_hit_tokens=40, prompt_cache_miss_tokens=60,
                                completion_tokens=10)
        msg = SimpleNamespace(content=json.dumps({"ok": True}))
        return SimpleNamespace(usage=usage, choices=[SimpleNamespace(message=msg)])


@pytest.fixture
def make_client(tmp_path, monkeypatch):
    fake = FakeCompletions()

    class FakeOpenAI:
        def __init__(self, **kw):
            self.chat = SimpleNamespace(completions=fake)

    import openai
    monkeypatch.setattr(openai, "OpenAI", FakeOpenAI)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    conn = connect(tmp_path / "d.db")
    init_schema(conn)

    def make(**llm):
        from devradar.llm import DeepSeekClient
        cfg = load_config(tmp_path / "none.yaml")
        cfg["llm"].update(llm)
        return DeepSeekClient(conn, cfg), fake
    return make


def test_thinking_disabled_by_default(make_client):
    client, fake = make_client()
    assert client.complete_json("s", "u") == {"ok": True}
    assert fake.kwargs["extra_body"] == {"thinking": {"type": "disabled"}}
    assert "reasoning_effort" not in fake.kwargs


def test_thinking_enabled_sends_effort(make_client):
    client, fake = make_client(thinking="enabled", reasoning_effort="low")
    client.complete_json("s", "u")
    assert fake.kwargs["extra_body"] == {"thinking": {"type": "enabled"}}
    assert fake.kwargs["reasoning_effort"] == "low"
    assert "temperature" not in fake.kwargs   # ignored in thinking mode, so not sent


def test_medium_effort_rejected(make_client):
    with pytest.raises(ValueError, match="medium"):
        make_client(thinking="enabled", reasoning_effort="medium")
