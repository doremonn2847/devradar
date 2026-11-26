"""Configuration loading with defaults from the design spec."""
from __future__ import annotations

import copy
import os
from pathlib import Path

import yaml

DEFAULTS: dict = {
    "timezone": "Asia/Bangkok",
    "db_path": "%LOCALAPPDATA%/devradar/devradar.db",
    "collector": {
        "interval_min": 20,
        "finished_after_min": 30,
        "roots": {
            "claude_code": "~/.claude/projects",
            "codex": "~/.codex/sessions",
        },
    },
    "excerpt_max_tokens": 4000,
    "llm_backfill_days": 14,  # older finished sessions get an excerpt but no LLM call (first install)
    "llm": {
        "base_url": "https://api.deepseek.com",
        "model": "deepseek-flash",
        "api_key_env": "DEEPSEEK_API_KEY",
    },
    "cost": {
        "soft_cap_usd": 6,
        "prices": {
            "off_peak": {"hit": 0.003, "miss": 0.15, "output": 0.6},
            "peak": {"hit": 0.006, "miss": 0.3, "output": 1.2},
        },
    },
    "rules": {
        "cooldown_days": 3,
        "stalled_days": 3,
        "stalled_max_days": 21,
        "deadline_window_days": 14,
        "deadline_idle_days": 2,
        "loop_min_sessions": 3,
        "loop_window_days": 7,
        "dangling_hours": 24,
        "spread_min_projects": 4,
        "spread_window_days": 3,
    },
    "news": {
        "dedupe_days": 14,
        "candidates": 30,
        "trending_languages": ["", "python", "typescript"],
        "watched_repos": [
            "anthropics/claude-code",
            "openai/codex",
            "stablyai/orca",
            "pingdotgg/t3code",
            "NousResearch/hermes-agent",
            "openclaw/openclaw",
        ],
        "watchlist": [],
        "keywords": ["agent", "llm", "rag", "mcp", "claude", "codex", "retrieval", "nlp", "python", "typescript"],
    },
    "hermes": {
        "alert_command": ["hermes", "cron", "run", "devradar-alerts"],
    },
}


def _merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def expand_path(p: str) -> Path:
    """Expand ~ and %VAR% / $VAR. %LOCALAPPDATA% falls back to ~/.local/share off Windows."""
    if "%LOCALAPPDATA%" in p and "LOCALAPPDATA" not in os.environ:
        p = p.replace("%LOCALAPPDATA%", str(Path.home() / ".local" / "share"))
    if "%USERPROFILE%" in p and "USERPROFILE" not in os.environ:
        p = p.replace("%USERPROFILE%", str(Path.home()))
    return Path(os.path.expandvars(os.path.expanduser(p)))


def default_config_path() -> Path:
    if os.environ.get("DEVRADAR_CONFIG"):
        return Path(os.environ["DEVRADAR_CONFIG"])
    appdata = os.environ.get("APPDATA")
    base = Path(appdata) if appdata else Path.home() / ".config"
    return base / "devradar" / "config.yaml"


def load_config(path: Path | None = None) -> dict:
    path = path or default_config_path()
    data = {}
    if path.exists():
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return _merge(DEFAULTS, data)
