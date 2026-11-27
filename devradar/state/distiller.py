"""Turn a session record into a compact excerpt for the LLM."""
from __future__ import annotations

from devradar.models import SessionRecord


def _tokens(text: str) -> int:
    return len(text) // 4


def _render(prompts, todos, errors, files, turns) -> str:
    parts = ["PROMPTS"] + [f"- {p}" for p in prompts]
    parts += ["", "OPEN TODOS"] + [f"- {t}" for t in todos]
    parts += ["", "ERRORS"] + [f"- {e}" for e in errors]
    parts += ["", "FILES"] + [f"- {f}" for f in files]
    parts += ["", "LAST ASSISTANT TURNS"] + [f"- {t}" for t in turns]
    return "\n".join(parts)


def distill(rec: SessionRecord, max_tokens: int = 4000) -> str:
    """Sections: PROMPTS, OPEN TODOS, ERRORS, FILES, LAST ASSISTANT TURNS.

    Over budget: drop oldest prompts first, then cut assistant turns from the front.
    Open todos are never dropped.
    """
    prompts = [p[:1500] for p in rec.user_prompts]
    todos = list(rec.open_todos)
    errors = rec.errors[-10:]
    files = sorted(rec.files_touched)[:30]
    turns = list(rec.assistant_tail)
    out = _render(prompts, todos, errors, files, turns)
    while _tokens(out) > max_tokens and len(prompts) > 1:
        prompts.pop(0)
        out = _render(prompts, todos, errors, files, turns)
    while _tokens(out) > max_tokens and turns:
        excess = (_tokens(out) - max_tokens) * 4 + 4
        if len(turns[0]) > excess:
            turns[0] = "…" + turns[0][excess:]
        else:
            turns.pop(0)
        out = _render(prompts, todos, errors, files, turns)
    return out
