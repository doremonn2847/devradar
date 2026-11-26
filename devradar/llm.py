"""LLM client protocol and the DeepSeek implementation (OpenAI-compatible API)."""
from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from typing import Protocol

from devradar.usage import record_usage


class LLMClient(Protocol):
    def complete_json(self, system: str, user: str) -> dict: ...


class DeepSeekClient:
    def __init__(self, conn: sqlite3.Connection, cfg: dict):
        from openai import OpenAI

        llm = cfg["llm"]
        key = os.environ.get(llm["api_key_env"])
        if not key:
            raise RuntimeError(f"{llm['api_key_env']} is not set")
        self.client = OpenAI(base_url=llm["base_url"], api_key=key, timeout=60)
        self.model = llm["model"]
        self.conn = conn
        self.prices = cfg["cost"]["prices"]

    def complete_json(self, system: str, user: str) -> dict:
        r = self.client.chat.completions.create(
            model=self.model,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            response_format={"type": "json_object"},
            temperature=0.2,
        )
        u = r.usage
        if u is not None:
            hit = getattr(u, "prompt_cache_hit_tokens", 0) or 0
            miss = getattr(u, "prompt_cache_miss_tokens", None)
            if miss is None:
                miss = max((u.prompt_tokens or 0) - hit, 0)
            record_usage(self.conn, hit, miss, u.completion_tokens or 0,
                         datetime.now(timezone.utc), self.prices)
        content = r.choices[0].message.content or ""
        try:
            data = json.loads(content)
        except json.JSONDecodeError as e:
            raise ValueError(f"model returned non-JSON: {content[:200]}") from e
        if not isinstance(data, dict):
            raise ValueError("model returned JSON that is not an object")
        return data
