from devradar.models import SessionRecord
from devradar.state.distiller import distill


def test_distill_budget_keeps_todos():
    rec = SessionRecord(agent="claude_code", session_id="s", path="p",
                        user_prompts=[f"prompt {i} " + "x" * 400 for i in range(200)],
                        assistant_tail=["y" * 3000] * 5,
                        open_todos=["Deploy to prod"], errors=["boom"])
    out = distill(rec)
    assert len(out) // 4 <= 4000
    assert "Deploy to prod" in out
    assert "prompt 199" in out      # newest prompt kept
    assert "prompt 0 " not in out   # oldest trimmed first


def test_distill_sections_order():
    rec = SessionRecord(agent="claude_code", session_id="s", path="p", user_prompts=["hi"],
                        assistant_tail=["done"], open_todos=["t"], errors=["e"], files_touched={"a.py"})
    out = distill(rec)
    idx = [out.index(h) for h in ("PROMPTS", "OPEN TODOS", "ERRORS", "FILES", "LAST ASSISTANT TURNS")]
    assert idx == sorted(idx)
