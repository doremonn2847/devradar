"""Dataclasses shared across DevRadar modules."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime


def _dt(v):
    return datetime.fromisoformat(v) if isinstance(v, str) else v


@dataclass
class SessionRecord:
    agent: str
    session_id: str
    path: str
    cwd: str | None = None
    started_at: datetime | None = None
    last_event_at: datetime | None = None
    user_prompts: list[str] = field(default_factory=list)
    assistant_tail: list[str] = field(default_factory=list)  # last 5 assistant texts
    errors: list[str] = field(default_factory=list)
    files_touched: set[str] = field(default_factory=set)
    open_todos: list[str] = field(default_factory=list)
    ended_on_question: bool = False
    orchestrated: bool = False        # launched by another agent (agy under Claude): sub-work
    agy_launches: list[str] = field(default_factory=list)  # ISO times this session ran `agy`
    cwd_source: str = ""              # "explicit" | "inferred"

    def to_json_dict(self) -> dict:
        d = asdict(self)
        d["files_touched"] = sorted(self.files_touched)
        for k in ("started_at", "last_event_at"):
            d[k] = d[k].isoformat() if d[k] else None
        return d

    @classmethod
    def from_json_dict(cls, d: dict) -> "SessionRecord":
        d = dict(d)
        d["files_touched"] = set(d.get("files_touched") or [])
        d["started_at"] = _dt(d.get("started_at"))
        d["last_event_at"] = _dt(d.get("last_event_at"))
        return cls(**d)


@dataclass
class ProjectState:
    goal: str = ""
    done: list[dict] = field(default_factory=list)  # {"item", "session_id"}
    next: list[str] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)
    deadlines: list[dict] = field(default_factory=list)  # {"item", "date"}


@dataclass
class Alert:
    rule: str
    project: str
    reason: str
    created_at: datetime


@dataclass
class NewsItem:
    source: str
    title: str
    url: str
    summary: str = ""
    published_at: datetime | None = None
    kind: str = "eng"  # "eng" | "research"
