"""DeepSeek token cost accounting (peak/off-peak pricing)."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from devradar.config import DEFAULTS

TZ = ZoneInfo("Asia/Bangkok")
PEAK_HOURS_UTC = ((1, 4), (6, 10))  # [start, end) hours, Mon–Fri


def is_peak(at: datetime) -> bool:
    u = at.astimezone(timezone.utc)
    if u.weekday() >= 5:
        return False
    return any(s <= u.hour < e for s, e in PEAK_HOURS_UTC)


def cost_usd(hit: int, miss: int, output: int, at: datetime, prices: dict | None = None) -> float:
    prices = prices or DEFAULTS["cost"]["prices"]
    p = prices["peak" if is_peak(at) else "off_peak"]
    return (hit * p["hit"] + miss * p["miss"] + output * p["output"]) / 1_000_000


def record_usage(conn: sqlite3.Connection, hit: int, miss: int, output: int, at: datetime,
                 prices: dict | None = None) -> float:
    c = cost_usd(hit, miss, output, at, prices)
    conn.execute("INSERT INTO usage(at, hit, miss, output, cost_usd) VALUES(?,?,?,?,?)",
                 (at.astimezone(timezone.utc).isoformat(), hit, miss, output, c))
    conn.commit()
    return c


def month_cost(conn: sqlite3.Connection, now: datetime) -> float:
    local = now.astimezone(TZ)
    total = 0.0
    for row in conn.execute("SELECT at, cost_usd FROM usage"):
        t = datetime.fromisoformat(row["at"]).astimezone(TZ)
        if (t.year, t.month) == (local.year, local.month):
            total += row["cost_usd"] or 0
    return total
