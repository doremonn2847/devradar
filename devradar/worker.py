"""Background worker: collect sessions, update state, run rules, gather news, wake Hermes on alerts."""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path

from devradar.collector.scan import collect
from devradar.config import expand_path, load_config
from devradar.db import connect, init_schema
from devradar.llm import LLMClient
from devradar.models import SessionRecord
from devradar.news import refresh_news
from devradar.rules import evaluate_rules, store_alerts
from devradar.state.distiller import distill
from devradar.state.project_state import update_project_state
from devradar.usage import month_cost

log = logging.getLogger("devradar")


def run_cycle(conn: sqlite3.Connection, cfg: dict, llm: LLMClient | None, now: datetime):
    finished = collect(conn, cfg, now)
    over_cap = month_cost(conn, now) >= cfg["cost"]["soft_cap_usd"]
    if over_cap and finished:
        log.info("soft cap reached; skipping LLM state updates for %d sessions", len(finished))
    for sid in finished:
        row = conn.execute("SELECT project, record_json FROM sessions WHERE session_id=?", (sid,)).fetchone()
        rec = SessionRecord.from_json_dict(json.loads(row["record_json"]))
        conn.execute("UPDATE sessions SET excerpt=? WHERE session_id=?",
                     (distill(rec, cfg["excerpt_max_tokens"]), sid))
        conn.commit()
        if llm is not None and not over_cap and row["project"] != "unknown":
            try:
                update_project_state(conn, row["project"], sid, llm)
            except Exception:  # network/API errors must not stop the cycle
                log.exception("state update failed for %s", sid)
    return evaluate_rules(conn, now, cfg)


def trigger_hermes(conn: sqlite3.Connection, cfg: dict) -> bool:
    """Run the Hermes alert job if any alert is waiting. Alerts are marked notified only on success."""
    ids = [r["id"] for r in conn.execute("SELECT id FROM alerts WHERE notified=0 AND acked=0")]
    if not ids:
        return False
    cmd = list(cfg["hermes"]["alert_command"])
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.SubprocessError) as e:
        log.error("hermes trigger failed: %s", e)
        return False
    if r.returncode != 0:
        log.error("hermes trigger exited %s: %s", r.returncode, (r.stderr or "")[-500:])
        return False
    conn.executemany("UPDATE alerts SET notified=1 WHERE id=?", [(i,) for i in ids])
    conn.commit()
    return True


def news_job(conn: sqlite3.Connection, cfg: dict, now: datetime) -> None:
    alerts = refresh_news(conn, cfg, now)
    store_alerts(conn, alerts, now, cfg["rules"]["cooldown_days"])


def _setup_logging() -> None:
    base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / ".local" / "share") / "devradar"
    base.mkdir(parents=True, exist_ok=True)
    handlers: list[logging.Handler] = [
        RotatingFileHandler(base / "worker.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8")]
    if sys.stderr is not None:  # pythonw (Task Scheduler) has no console
        handlers.append(logging.StreamHandler())
    logging.basicConfig(level=logging.INFO, handlers=handlers, force=True,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def main() -> None:
    from apscheduler.schedulers.blocking import BlockingScheduler

    _setup_logging()
    cfg = load_config()
    conn = connect(expand_path(cfg["db_path"]))
    init_schema(conn)
    llm = None
    try:
        from devradar.llm import DeepSeekClient
        llm = DeepSeekClient(conn, cfg)
    except RuntimeError as e:
        log.warning("%s; running without LLM state updates", e)

    def utcnow():
        return datetime.now(timezone.utc)

    def cycle():
        try:
            run_cycle(conn, cfg, llm, utcnow())
        except Exception:
            log.exception("cycle failed")
        trigger_hermes(conn, cfg)

    def news():
        try:
            news_job(conn, cfg, utcnow())
        except Exception:
            log.exception("news job failed")
        trigger_hermes(conn, cfg)

    from apscheduler.executors.pool import ThreadPoolExecutor

    # One worker thread: jobs share one sqlite connection, so they must never run at the same time.
    sched = BlockingScheduler(timezone=cfg["timezone"], executors={"default": ThreadPoolExecutor(1)})
    common = {"misfire_grace_time": 3600, "coalesce": True, "max_instances": 1}
    sched.add_job(cycle, "interval", minutes=cfg["collector"]["interval_min"], next_run_time=datetime.now(timezone.utc), **common)
    sched.add_job(news, "cron", hour=6, minute=45, **common)
    sched.add_job(news, "cron", hour=18, minute=0, **common)
    log.info("devradar worker started")
    sched.start()


if __name__ == "__main__":
    main()
