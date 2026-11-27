# DevRadar

A background bot that reads your coding-agent sessions (Claude Code and Codex — whether you
run them directly, in Orca or in T3 Code — plus Antigravity's `agy` CLI and the Antigravity app), keeps a progress record per project, pings you on
Telegram when something looks off, and sends a daily tech radar. Telegram, scheduling and
replies are handled by [Hermes Agent](https://github.com/NousResearch/hermes-agent) on
DeepSeek Flash; this repo is the worker and the tools Hermes uses.

Design: `docs/superpowers/specs/2026-11-27-devradar-design.md`

## Install (Windows)

1. Python 3.12+ from python.org (tick "Add to PATH").
2. In this folder:
   ```powershell
   pip install -e .
   setx DEEPSEEK_API_KEY sk-...
   ```
   Open a new terminal afterwards so the key is visible.
3. Optional: copy `config.example.yaml` to `%APPDATA%\devradar\config.yaml` and edit.
4. Quick check — run the worker in the foreground for one cycle, then Ctrl+C:
   ```powershell
   devradar
   ```
   `%LOCALAPPDATA%\devradar\worker.log` should list your sessions being processed.
5. Start it at logon: `powershell -ExecutionPolicy Bypass -File scripts\install-tasks.ps1`
6. Set up Hermes: follow `hermes\setup.md`.

## How it works

- Every 20 min the worker reads new data from `%USERPROFILE%\.claude\projects`,
  `%USERPROFILE%\.codex\sessions`, `%USERPROFILE%\.gemini\antigravity-cli\brain` (agy CLI) and
  `%USERPROFILE%\.gemini\antigravity*\brain` (Antigravity app). Orca worktrees fold into their main repo.
- `agy` conversations started by a Claude (or Codex) session count as that session's sub-work:
  they inherit its project and add files/errors, but get no summary or "dangling" alert of their own.
- Antigravity app conversations have no chat log; open `- [ ]` items in `task.md` become todos and
  `walkthrough.md` counts as what got done.
- When a session has been quiet for 30 min, it's distilled to a ~4K-token excerpt and DeepSeek
  updates that project's goal / done / next / blockers / deadlines. Every "done" item must cite
  a session it came from.
- Five plain-code rules raise alerts (stalled, deadline drift, repeated error, dangling session,
  spread thin), each with a 3-day cooldown. A new alert makes the worker run Hermes's
  `devradar-alerts` job, so there's no AI call when nothing is wrong.
- At 06:45 and 18:00 it gathers news (GitHub trending, Hacker News, releases of the tools you
  use, Hugging Face papers). Hermes picks 5–7 at 07:30.
- Soft cap: past $6/month of worker spend, the worker stops calling DeepSeek. Ask the bot
  "usage?" to see this month's spend.

## Tell it things on Telegram

- "DocuSense deadline is 2026-12-20" → pinned; the summarizer won't override it
- "snooze Đề Cũ 3" → no alerts for that project for 3 days
- "more 2" → deeper look at radar item 2
- "git status in de-cu" → runs on your laptop; anything risky waits for your yes (see `hermes\setup.md` §5)

## Dev

```bash
pip install -e ".[dev]"
pytest
```

The parser fixtures in `tests/fixtures/` are synthetic, built from public descriptions of each
format. Before trusting the parsers, add one redacted real session from each of `.claude\projects`,
`.codex\sessions`, `.gemini\antigravity-cli\brain` and `.gemini\antigravity*\brain`, and re-run the
tests. Also check how your orchestrator launches `agy`: linking looks for an `agy` command in the
Claude session's Bash/PowerShell tool calls.

## Uninstall

`powershell -ExecutionPolicy Bypass -File scripts\uninstall-tasks.ps1`, then `hermes gateway uninstall`.
