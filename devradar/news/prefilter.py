"""Cheap keyword scoring before the LLM sees anything; keeps an ~80/20 eng/research mix."""
from __future__ import annotations

from devradar.models import NewsItem

WEIGHTS = {"hn": 1.0, "trending": 1.0, "hf": 1.0}
RELEASE_WEIGHT = 3.0
RESEARCH_SHARE = 0.2


def score(item: NewsItem, keywords: list[str]) -> float:
    text = f"{item.title} {item.summary}".lower()
    hits = sum(1 for k in keywords if k and k.lower() in text)
    weight = RELEASE_WEIGHT if item.source.startswith("release:") else WEIGHTS.get(item.source, 1.0)
    return hits + weight


def prefilter(items: list[NewsItem], keywords: list[str], limit: int = 30) -> list[tuple[NewsItem, float]]:
    scored = sorted(((i, score(i, keywords)) for i in items), key=lambda p: -p[1])
    research = [p for p in scored if p[0].kind == "research"]
    eng = [p for p in scored if p[0].kind != "research"]
    n_res = min(len(research), round(limit * RESEARCH_SHARE))
    n_eng = min(len(eng), limit - n_res)
    n_res = min(len(research), limit - n_eng)  # backfill with research if eng runs short
    picked = eng[:n_eng] + research[:n_res]
    return sorted(picked, key=lambda p: -p[1])
