# DevRadar — notes for Claude Code

Background bot for one user (Windows laptop, Asia/Bangkok). A Python **worker** reads coding-agent
session files, keeps a grounded progress record per project, raises "something's off" alerts and
gathers tech news. **Hermes Agent** (DeepSeek Flash) talks to the user on Telegram, using this repo's
**MCP tools**. Design: `docs/superpowers/specs/2026-11-27-devradar-design.md` (incl. §10 addendum).
Plan: `docs/superpowers/plans/2026-11-27-devradar.md`. Setup: `README.md`, `hermes/setup.md`.

Work happens on branch `feat/devradar` (PR #1 into `master`, github.com/doremonn2847/devradar).

## Commands

```powershell
pip install -e ".[dev]"   # user's laptop uses Anaconda (base) Python
pytest                    # 106 tests, ~2 s
devradar                  # worker in the foreground (first cycle runs immediately; Ctrl+C stops)
devradar-mcp              # MCP server on stdio (Hermes starts it)
```

## Layout

- `devradar/collector/` — readers. `scan.py` orchestrates; per agent:
  - `claude_code.py` — `~/.claude/projects/**.jsonl`; skips `subagents/` and `agent-*.jsonl`
    (they reuse the parent sessionId) and `isSidechain` lines; records `agy` launches.
  - `codex.py` — `~/.codex/sessions/**`. Errors/files come from `event_msg` → `item_completed`
    (`CommandExecution.exit_code`, `FileChange.changes`). `originator == codex_exec` ⇒ orchestrated.
  - `agy_cli.py` — Antigravity transcripts (`brain/<id>/.system_generated/logs/transcript.jsonl`),
    used for both the `agy` CLI and the Antigravity app. Errors = `RUN_COMMAND` with `exit code: N≠0`.
  - `agy_workspace.py` — workspace from `<root>/conversations/<id>.db` (file:/// URL in
    `trajectory_metadata_blob`). Priority: db workspace > explicit `Cwd` arg > inferred from paths
    > link to the launching Claude/Codex session (`scan.link_orchestrated`).
  - `agy_app.py` — `task.md` open items as todos (layered on the transcript); md-only fallback.
  - `repo.py` — cwd → main git repo (worktrees fold in; deleted scratch dirs walk up to the repo).
- `devradar/state/` — `distiller.py` (≤4K-token excerpt), `project_state.py` (DeepSeek → JSON
  state; every "done" must cite the session it was shown; pinned facts win).
- `devradar/rules/` — stalled, deadline, loop, dangling, spread; 3-day cooldown, snooze.
- `devradar/news/` — HN, GitHub trending, watched-tool releases, HF papers; dedupe, prefilter.
- `devradar/mcp_server.py` — `Tools` class on mcp 2.x `MCPServer` (not FastMCP).
- `devradar/worker.py` — APScheduler (one thread, shared sqlite conn), Hermes trigger, cost cap.
- `hermes/skills/devradar/SKILL.md`, `hermes/setup.md`, `scripts/install-tasks.ps1`.

## Decisions to keep

- **Orchestrated sessions** (`agy` CLI, `codex exec`) are sub-work: they add activity/files/errors
  but get no LLM summary and no dangling/question alerts. The user drives Claude Code and the
  Antigravity app directly; agy/codex exec are launched by their Claude orchestrator.
- **DeepSeek**: thinking `disabled` for the worker (default would be high effort, billed as
  output); `reasoning_effort` low|high|max only — "medium" is refused (DeepSeek maps it to high).
- **Cost**: soft cap $6/month re-checked before every worker LLM call; no LLM backfill for sessions
  older than 14 days; stalled alerts skip projects idle > 21 days.
- **Telegram commands**: user enabled shell/file tools in chat (Telegram behind Face ID) with
  `approvals.mode: manual`; scheduled jobs get no shell and `cron_mode: deny`, because they read
  untrusted text (news, transcripts).
- Release alerts: first sighting of a repo's releases is a silent baseline; alert on major/minor bump.

## Conventions

- TDD: failing test first, then code; run the whole suite before committing.
- Fixtures `tests/fixtures/*_real.jsonl` were derived from the user's real files with all text and
  paths replaced. Keep it that way: never commit real session content, names or paths, and never
  open credential files (`auth.json`, `oauth_creds.json`, `clerk-tokens.json`, `.env`).
- Timestamps are always tz-aware UTC internally (`collector/common.parse_ts`).

## Setup status (user's laptop, 2026-11-27)

- Worker ran once successfully (first full scan ~48 s). `DEEPSEEK_API_KEY` was not yet visible
  (needs `setx` + a new terminal). At least one alert is queued, waiting for Hermes.
- Hermes v0.23.4 installed natively: binary `%LOCALAPPDATA%\hermes\bin\hermes.exe`, config
  `%LOCALAPPDATA%\hermes\config.yaml` (not `~/.hermes`). `devradar` added under `mcp_servers`;
  `hermes mcp test devradar` passes.
- **Current blocker**: in chat sessions MCP reports `mcp_servers_unavailable`. Hermes's
  `logs/mcp-stderr.log` shows another configured server, the AWS MCP proxy
  (`mcp_proxy_for_aws`), failing with an expired session ("reauthenticate using 'aws login'"),
  which takes the whole MCP step down. Fix: `enabled: false` on that server (recommended) or
  `aws login`; then restart `hermes` (and the gateway) rather than `/reload-mcp`.
- If Anaconda's Python can't load DLLs when Hermes spawns `devradar-mcp`, add an `env: PATH:` with
  the anaconda root, `Library\bin` and `Scripts` to the server entry.

## Not yet verified

- `hermes cron run` / paused-job behaviour, per-platform toolsets, approval buttons on Telegram.
- `scripts/install-tasks.ps1` (never run; no PowerShell in the build environment).
- Whether Hermes's `hermes model` exposes a reasoning-effort choice (setup.md asks for low).

## Known minor gaps (deferred)

- Deadline "days left" uses the UTC date (off by one 00:00–07:00 Bangkok).
- Project `done` list grows without limit in prompts and `get_project` output.
- A session's cwd is the last one seen; a session that moves repos moves its whole history.
- No backoff when the Hermes trigger keeps failing (retried every 20 min).
- `len // 4` token estimate undercounts Vietnamese (~2×).
- First scan runs `git` subprocesses inside per-file write transactions.
- A session file replaced by a larger file isn't detected (resumes mid-line).
- Incremental scans over a large history still take ~25 s on a slow file share; untested on native Windows.
