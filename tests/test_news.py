from datetime import datetime, timedelta, timezone

import httpx
import pytest
import respx

from devradar.config import load_config
from devradar.db import connect, init_schema
from devradar.models import NewsItem
from devradar.news import refresh_news
from devradar.news.dedupe import dedupe
from devradar.news.prefilter import prefilter
from devradar.news.sources import fetch_github_trending, fetch_hf_papers, fetch_hn, fetch_releases

NOW = datetime(2026, 11, 27, 0, 0, tzinfo=timezone.utc)

HN = {"hits": [{"title": "Show HN: a tiny MCP server", "url": "https://ex.com/mcp", "objectID": "1"},
               {"title": "Ask HN: no url", "url": None, "objectID": "2"}]}
TRENDING = """<html><body>
<article class="Box-row"><h2 class="h3 lh-condensed"><a href="/acme/vecdb"> acme / vecdb </a></h2>
<p class="col-9 color-fg-muted my-1 pr-4">Local-first vector DB for RAG</p></article>
</body></html>"""
HF = [{"paper": {"id": "2611.00001", "title": "Cheaper long-context retrieval", "summary": "We show..."},
       "publishedAt": "2026-11-26T00:00:00.000Z"}]


def atom(*tags):
    entries = "".join(
        f'<entry><title>{t}</title><link rel="alternate" href="https://github.com/o/r/releases/tag/{t}"/>'
        f"<updated>2026-11-2{i}T00:00:00Z</updated></entry>" for i, t in enumerate(tags))
    return f'<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom">{entries}</feed>'


@pytest.fixture
def env(tmp_path):
    conn = connect(tmp_path / "d.db")
    init_schema(conn)
    cfg = load_config(tmp_path / "none.yaml")
    cfg["news"]["watched_repos"] = ["o/r"]
    cfg["news"]["trending_languages"] = [""]
    return conn, cfg


def mock_all(router, release_tags=("v1.4.2",), hn_status=200):
    router.get("https://hn.algolia.com/api/v1/search").mock(
        return_value=httpx.Response(hn_status, json=HN if hn_status == 200 else {}))
    router.get("https://github.com/trending").mock(return_value=httpx.Response(200, text=TRENDING))
    router.get("https://huggingface.co/api/daily_papers").mock(return_value=httpx.Response(200, json=HF))
    router.get("https://github.com/o/r/releases.atom").mock(
        return_value=httpx.Response(200, text=atom(*release_tags)))


def test_sources_parse():
    with respx.mock() as r:
        mock_all(r)
        with httpx.Client() as c:
            hn = fetch_hn(c)
            tr = fetch_github_trending(c, [""])
            hf = fetch_hf_papers(c)
            rel = fetch_releases(c, ["o/r"])
    assert hn[0].url == "https://ex.com/mcp"
    assert hn[1].url == "https://news.ycombinator.com/item?id=2"
    assert tr[0].title == "acme/vecdb" and tr[0].url == "https://github.com/acme/vecdb"
    assert "vector DB" in tr[0].summary
    assert hf[0].kind == "research" and hf[0].url == "https://huggingface.co/papers/2611.00001"
    assert rel[0].source == "release:o/r" and rel[0].title == "o/r v1.4.2"


def test_dedupe_fuzzy_title(env):
    conn, _ = env
    conn.execute("INSERT INTO news_items(source, title, url, fetched_at) VALUES('hn', 'Hermes Agent 2.0 released', 'u1', ?)",
                 (NOW.isoformat(),))
    items = [NewsItem("hn", "Hermes Agent 2.0 Released!", "u2"), NewsItem("hn", "Something else", "u3")]
    assert [i.url for i in dedupe(conn, items, NOW)] == ["u3"]


def test_dedupe_window(env):
    conn, _ = env
    conn.execute("INSERT INTO news_items(source, title, url, fetched_at) VALUES('hn', 'Old', 'u1', ?)",
                 ((NOW - timedelta(days=20)).isoformat(),))
    assert len(dedupe(conn, [NewsItem("hn", "Old", "u9")], NOW)) == 1


def test_prefilter_mix():
    items = [NewsItem("hn", f"eng {i} agent", f"e{i}") for i in range(40)]
    items += [NewsItem("hf", f"paper {i}", f"r{i}", kind="research") for i in range(10)]
    out = prefilter(items, ["agent"], limit=30)
    assert len(out) == 30
    research = sum(1 for it, _ in out if it.kind == "research")
    assert 5 <= research <= 7


def test_source_failure_isolated(env):
    conn, cfg = env
    with respx.mock() as r:
        mock_all(r, hn_status=500)
        refresh_news(conn, cfg, NOW)
    titles = {row["title"] for row in conn.execute("SELECT title FROM news_items")}
    assert "acme/vecdb" in titles and "Cheaper long-context retrieval" in titles


def test_release_alert_on_minor_bump(env):
    conn, cfg = env
    with respx.mock() as r:
        mock_all(r, release_tags=("v1.4.2",))
        assert refresh_news(conn, cfg, NOW) == []          # first sighting = baseline
    with respx.mock() as r:
        mock_all(r, release_tags=("v1.5.0", "v1.4.2"))
        alerts = refresh_news(conn, cfg, NOW + timedelta(hours=12))
    assert len(alerts) == 1 and alerts[0].rule == "release" and "v1.5.0" in alerts[0].reason


def test_no_alert_on_patch(env):
    conn, cfg = env
    with respx.mock() as r:
        mock_all(r, release_tags=("v1.4.2",))
        refresh_news(conn, cfg, NOW)
    with respx.mock() as r:
        mock_all(r, release_tags=("v1.4.3", "v1.4.2"))
        assert refresh_news(conn, cfg, NOW + timedelta(hours=12)) == []


def test_release_not_fuzzy_deduped(env):
    conn, _ = env
    conn.execute("INSERT INTO news_items(source, title, url, fetched_at) VALUES"
                 "('release:anthropics/claude-code', 'anthropics/claude-code v2.0.9', 'u1', ?)", (NOW.isoformat(),))
    new = NewsItem("release:anthropics/claude-code", "anthropics/claude-code v2.1.0", "u2")
    assert dedupe(conn, [new], NOW) == [new]
