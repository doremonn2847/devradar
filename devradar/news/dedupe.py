"""Drop news items already seen in the last N days (same URL or near-identical title)."""
from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timedelta
from difflib import SequenceMatcher

from devradar.models import NewsItem


def _norm(t: str) -> str:
    return re.sub(r"[^\w\s.]", "", t.lower()).strip()


def dedupe(conn: sqlite3.Connection, items: list[NewsItem], now: datetime, days: int = 14) -> list[NewsItem]:
    cutoff = now - timedelta(days=days)
    seen_urls, seen_titles = set(), []
    for r in conn.execute("SELECT url, title, fetched_at FROM news_items"):
        if r["fetched_at"] and datetime.fromisoformat(r["fetched_at"]) >= cutoff:
            seen_urls.add(r["url"])
            seen_titles.append(_norm(r["title"] or ""))
    out = []
    for it in items:
        t = _norm(it.title)
        if it.url in seen_urls:
            continue
        # Release titles of one repo differ only by version, so they're deduped by URL alone.
        is_release = it.source.startswith("release:")
        if not is_release and any(SequenceMatcher(None, t, s).ratio() >= 0.9 for s in seen_titles):
            continue
        out.append(it)
        seen_urls.add(it.url)
        if not is_release:
            seen_titles.append(t)
    return out
