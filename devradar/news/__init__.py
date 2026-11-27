"""News scout: fetch, dedupe, prefilter, store; flag significant releases of watched tools."""
from __future__ import annotations

import logging
import re
import sqlite3
from datetime import datetime

import httpx

from devradar.models import Alert, NewsItem
from devradar.news.dedupe import dedupe
from devradar.news.prefilter import prefilter
from devradar.news.sources import fetch_github_trending, fetch_hf_papers, fetch_hn, fetch_releases

log = logging.getLogger(__name__)
_VER = re.compile(r"(\d+)\.(\d+)(?:\.(\d+))?")


def _version(title: str) -> tuple[int, int, int] | None:
    m = _VER.search(title.rsplit(" ", 1)[-1])
    return (int(m[1]), int(m[2]), int(m[3] or 0)) if m else None


def _known_versions(conn, source: str) -> list[tuple[int, int, int]]:
    out = []
    for r in conn.execute("SELECT title FROM news_items WHERE source=?", (source,)):
        v = _version(r["title"] or "")
        if v:
            out.append(v)
    return out


def _release_alerts(conn, releases: list[NewsItem], now: datetime) -> list[Alert]:
    alerts = []
    by_source: dict[str, list[NewsItem]] = {}
    for it in releases:
        by_source.setdefault(it.source, []).append(it)
    for source, items in by_source.items():
        prev = _known_versions(conn, source)
        if not prev:
            continue  # first sighting is the baseline
        top_prev = max(prev)
        for it in items:
            v = _version(it.title)
            if v and v[:2] > top_prev[:2]:
                alerts.append(Alert("release", source.split(":", 1)[1],
                                    f"New release: {it.title} — {it.url}", now))
                break
    return alerts


def _store(conn, items: list[tuple[NewsItem, float]], now: datetime) -> None:
    for it, sc in items:
        conn.execute(
            "INSERT OR IGNORE INTO news_items(source, title, url, summary, published_at, kind, score, fetched_at)"
            " VALUES(?,?,?,?,?,?,?,?)",
            (it.source, it.title, it.url, it.summary,
             it.published_at.isoformat() if it.published_at else None, it.kind, sc, now.isoformat()))


def refresh_news(conn: sqlite3.Connection, cfg: dict, now: datetime,
                 client: httpx.Client | None = None) -> list[Alert]:
    n = cfg["news"]
    own = client is None
    client = client or httpx.Client(timeout=15, headers={"User-Agent": "devradar"}, follow_redirects=True)
    try:
        repos = list(dict.fromkeys(n["watched_repos"] + n.get("watchlist", [])))
        releases = fetch_releases(client, repos)
        others = fetch_hn(client) + fetch_github_trending(client, n["trending_languages"]) + fetch_hf_papers(client)
    finally:
        if own:
            client.close()
    alerts = _release_alerts(conn, releases, now)
    keywords = list(n["keywords"]) + [r["display"] for r in conn.execute("SELECT display FROM projects") if r["display"]]
    fresh = dedupe(conn, releases + others, now, n["dedupe_days"])
    fresh_releases = [i for i in fresh if i.source.startswith("release:")]
    picked = prefilter([i for i in fresh if not i.source.startswith("release:")], keywords,
                       max(n["candidates"] - len(fresh_releases), 0))
    _store(conn, [(i, 3.0) for i in fresh_releases] + picked, now)
    conn.commit()
    return alerts
