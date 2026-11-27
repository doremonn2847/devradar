"""News source fetchers. Each returns [] on any failure so one bad source can't stop the rest."""
from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from datetime import datetime
from html.parser import HTMLParser

import httpx

from devradar.models import NewsItem

log = logging.getLogger(__name__)
ATOM = "{http://www.w3.org/2005/Atom}"


def _ts(v):
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except ValueError:
        return None


def _get(client: httpx.Client, url: str, **params) -> httpx.Response | None:
    try:
        r = client.get(url, params=params or None)
        r.raise_for_status()
        return r
    except httpx.HTTPError as e:
        log.warning("news fetch failed %s: %s", url, e)
        return None


def fetch_hn(client: httpx.Client) -> list[NewsItem]:
    out = []
    for tag in ("front_page", "show_hn"):
        r = _get(client, "https://hn.algolia.com/api/v1/search", tags=tag)
        if r is None:
            continue
        try:
            hits = r.json().get("hits", [])
        except ValueError:
            continue
        for h in hits:
            if not h.get("title"):
                continue
            url = h.get("url") or f"https://news.ycombinator.com/item?id={h.get('objectID')}"
            out.append(NewsItem("hn", h["title"], url, "", _ts(h.get("created_at")), "eng"))
    return out


class _TrendingParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.items: list[dict] = []
        self._in_row = self._in_h2 = self._in_desc = False

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        cls = a.get("class") or ""
        if tag == "article" and "Box-row" in cls:
            self._in_row = True
            self.items.append({"href": None, "desc": ""})
        elif self._in_row and tag == "h2":
            self._in_h2 = True
        elif self._in_row and self._in_h2 and tag == "a" and a.get("href") and not self.items[-1]["href"]:
            self.items[-1]["href"] = a["href"]
        elif self._in_row and tag == "p":
            self._in_desc = True

    def handle_endtag(self, tag):
        if tag == "h2":
            self._in_h2 = False
        elif tag == "p":
            self._in_desc = False
        elif tag == "article":
            self._in_row = False

    def handle_data(self, data):
        if self._in_desc and self.items:
            self.items[-1]["desc"] += data


def fetch_github_trending(client: httpx.Client, languages: list[str]) -> list[NewsItem]:
    out = []
    for lang in languages:
        url = "https://github.com/trending" + (f"/{lang}" if lang else "")
        r = _get(client, url, since="daily")
        if r is None:
            continue
        p = _TrendingParser()
        try:
            p.feed(r.text)
        except Exception as e:  # html.parser is lenient, but never let a page break the run
            log.warning("trending parse failed %s: %s", url, e)
            continue
        for it in p.items:
            if not it["href"]:
                continue
            repo = it["href"].strip("/")
            out.append(NewsItem("trending", repo, f"https://github.com/{repo}", " ".join(it["desc"].split()), None, "eng"))
    return out


def fetch_releases(client: httpx.Client, repos: list[str], per_repo: int = 3) -> list[NewsItem]:
    out = []
    for repo in repos:
        r = _get(client, f"https://github.com/{repo}/releases.atom")
        if r is None:
            continue
        try:
            root = ET.fromstring(r.text)
        except ET.ParseError:
            continue
        for e in root.findall(f"{ATOM}entry")[:per_repo]:
            link = e.find(f"{ATOM}link")
            href = link.get("href") if link is not None else None
            if not href:
                continue
            tag = href.rstrip("/").rsplit("/", 1)[-1]
            title = (e.findtext(f"{ATOM}title") or tag).strip()
            out.append(NewsItem(f"release:{repo}", f"{repo} {tag}", href, title,
                                _ts(e.findtext(f"{ATOM}updated")), "eng"))
    return out


def fetch_hf_papers(client: httpx.Client) -> list[NewsItem]:
    r = _get(client, "https://huggingface.co/api/daily_papers")
    if r is None:
        return []
    try:
        data = r.json()
    except ValueError:
        return []
    out = []
    for d in data if isinstance(data, list) else []:
        p = d.get("paper") or {}
        if not p.get("id") or not p.get("title"):
            continue
        out.append(NewsItem("hf", p["title"], f"https://huggingface.co/papers/{p['id']}",
                            (p.get("summary") or "")[:500], _ts(d.get("publishedAt")), "research"))
    return out
