# DevRadar — background Telegram assistant for build progress and tech news

Status: approved · 2026-11-27
Working name: **DevRadar** (rename freely)

## 1. Intent

The user plans and drives almost all project work through chats with coding agents
(Claude Code, run directly or through Orca; evaluating T3 Code; Codex possible).
Commits and the deployed sites lag behind what actually happens, so they are a poor
progress signal. The real record of progress is the agent session transcripts.

DevRadar is a background bot that:

1. **Tracks build progress** by reading those transcripts, keeps a per-project state,
   sends a daily recap on Telegram, and **pushes the user when something seems off**.
2. **Scouts tech news**: new tools and trends, engineering-heavy (~80%) and
   research-light (~20%), ranked by relevance to what the user is building right now.

Success = the user stops losing track of projects across parallel agent sessions, gets
nudged before a project silently stalls, and learns about tools that are useful to
their builds without having to look for them.

## 2. Goals, non-goals, anti-goals

**Goals**
- Works no matter which front-end is used (Claude Code CLI, Orca, T3 Code), because it
  reads the agents' own session files, not the front-end.
- Proactive: messages are sent without the user asking.
- Conversational: the user can talk to it on Telegram as a general agent ("why is X
  flagged?", "snooze X", "what did I do on Đề Cũ this week?", "dig into item 2").
- Low noise: at most one recap per evening, one digest per morning, plus rare
  immediate alerts.

**Non-goals (v1)**
- Reading claude.ai / ChatGPT web chats (no API; out of scope).
- Using git commits, CI or site monitoring as primary signals.
- Acting on projects (opening PRs, running agents). DevRadar observes and reports.
- Multi-user support.

**Anti-goals (failure even if it "works")**
- Spammy: a message per session, or the same alert every day.
- Hallucinated progress: summaries that claim things the transcript doesn't show.
- Leaking transcripts: raw session content sent anywhere beyond the LLM provider and
  the user's own Telegram chat.
- Runaway cost: an LLM call on every polling tick.
- Fragile lock-in: core logic tangled into Hermes.

## 3. Constraints

- **Platform: Windows, laptop only.** Missed scheduled jobs run on wake; acceptable.
- Session files live on the laptop:
  - Claude Code: `%USERPROFILE%\.claude\projects\<encoded-cwd>\<session-id>.jsonl`
  - Codex: `%USERPROFILE%\.codex\sessions\**\*.jsonl`
  - T3 Code drives Claude through Anthropic's Agent SDK. **To verify:** whether those
    sessions are also written under `.claude\projects`. If not, add a T3 Code adapter.
- Orca runs each feature in its own git worktree, so one repo appears under many cwds.
  Sessions are grouped by **repository**, not by directory.
- Transcripts are large and noisy. Only distilled excerpts are sent to the LLM.
- **LLM provider: DeepSeek Flash** (`deepseek-flash`), via its OpenAI-compatible API.
  Excerpts are processed on DeepSeek's servers; the user accepted this.
- Budget target: about $3–4 a month.

## 4. Approaches considered

| | Approach | Trade-off |
|---|---|---|
| **A (chosen)** | **Python collector + Hermes Agent** (Telegram gateway, cron, replies, LLM) on **DeepSeek Flash** | Full general agent on Telegram, least plumbing; ~12K-token fixed overhead per call, made cheap by DeepSeek's cache-hit price; est. $3–4/month |
| B | Python collector + OpenClaw | User knows OpenClaw; but a second stack (Node/TS) to maintain. Fallback. |
| C | Lean custom bot (python-telegram-bot + direct LLM calls) on Groq free tier | Free and ~10× fewer tokens; but replies limited to fixed tools, and free-tier per-minute caps are tight |

Chosen: **A**. The user wanted to compare paid costs for Hermes, then picked Hermes
with DeepSeek Flash. C remains the fallback if costs or reliability disappoint: the
collector and tools are shared, so only the messaging layer changes.

## 5. Design

### 5.1 Components

```
 .claude\projects\*.jsonl ─┐
 .codex\sessions\**.jsonl ─┼─► [Collector] ─► state\devradar.db (SQLite)
                           │        │
 news sources (HTTP) ──────┼─► [News scout] ─► state (news tables)
                                    │
                       [Rules engine] ─► alert queue
                                    │
                     [devradar MCP server]  ◄── tools for Hermes
                                    │
          [Hermes Agent: gateway (Telegram) + cron + DeepSeek Flash]
               ├─ 21:30 evening recap
               ├─ 07:30 morning radar
               ├─ alerts: woken only when the queue is non-empty
               └─ replies: general agent + DevRadar tools
```

Two background processes, both started at logon by Windows Task Scheduler:

1. **devradar worker** (Python): runs the collector every 20 min, the news fetch
   twice a day, the rules after each collect. Pure code, no LLM, except the
   project-state update in §5.3, which it requests through a small direct DeepSeek call.
2. **Hermes gateway**: Telegram plus its cron scheduler. Connects to the devradar MCP
   server for tools.

### 5.2 Collector (plain Python, no LLM)

- Incremental: stores the byte offset per session file; parses only new lines.
- A session counts as "finished" after 30 min with no new lines.
- Per session it extracts: repo (resolve `cwd` → `git rev-parse --show-toplevel` →
  main worktree via `git worktree list`, falling back to the cwd), start/end, user
  prompts, final assistant turns, error messages seen, files touched (from tool-call
  metadata), open todo lists, and whether it ended on an unanswered question.
- SQLite tables: `sessions`, `projects`, `events`, `alerts`, `news_items`, `facts`.
- **Distiller:** per finished session, a compact excerpt (user prompts + last
  assistant turns + todo lists), capped at ~4K tokens.

### 5.3 Project state (LLM, grounded)

- When a session finishes, the worker sends the excerpt + the current project state
  to DeepSeek Flash directly (not through Hermes, to skip the 12K agent overhead) and
  gets back an updated **goal, done, next, blockers, deadlines** as JSON.
- Grounding rule: every "done" item cites a session id; the prompt forbids claims the
  excerpt doesn't contain. Responses that fail JSON-schema validation are retried
  once, then dropped (state stays as it was).
- User-pinned facts (from Telegram, e.g. "DocuSense deadline Dec 20") live in `facts`
  and the LLM never overwrites them.

### 5.4 Rules engine — "something's off" (plain code)

| Rule | Fires when | Default |
|---|---|---|
| Stalled next step | the "next" item is unchanged and there were no sessions for N days | N = 3 |
| Deadline drift | a deadline is ≤ 14 days away and there was no activity for N days | N = 2 |
| Loop | the same error signature appears in ≥ 3 sessions within 7 days | — |
| Dangling session | a session ended mid-task (open todo, or unanswered question) and wasn't resumed within 24 h | — |
| Spread thin | ≥ 4 projects active in the last 3 days and none moved anything to "done" | — |

- Cooldown 3 days per rule per project; snooze from Telegram. Thresholds in
  `config.yaml`.
- Alerts go to the `alerts` table. The worker wakes Hermes only when a new alert is
  written (runs `hermes` cron job on demand, or posts to the gateway), so there is no
  LLM call when nothing is wrong.

### 5.5 News scout

- Sources (v1): GitHub trending (daily; Python, TypeScript, all), Hacker News front
  page + Show HN, release feeds for watched tools (Claude Code, Codex, Orca, T3 Code,
  Hermes, OpenClaw) + a user-editable watchlist, Hugging Face daily papers (NLP,
  agents, retrieval).
- Worker: fetch → dedupe against the last 14 days → keyword pre-filter → top ~30
  candidates stored.
- Hermes morning job: reads candidates + current project states via tools, picks 5–7
  (≈ 80/20 engineering/research), writes one "why it matters to you" line each.
- Immediate alert only for a major release of a watched tool.
- 👍/👎 or "less of this" replies are stored as preferences and used in ranking.

### 5.6 Hermes configuration

- Model: `deepseek-flash` through Hermes's custom OpenAI-compatible endpoint
  (`OPENAI_BASE_URL` + `OPENAI_API_KEY` in `~/.hermes/.env`).
- Telegram: bot token + `TELEGRAM_ALLOWED_USERS` restricted to the user's id.
- **Trim tools:** disable Hermes built-ins not needed (browser, image gen, TTS, code
  execution, etc.) to cut the per-call overhead; keep memory, messaging, cron,
  web fetch, and the devradar MCP server.
- Cron jobs (Asia/Bangkok): recap 21:30, radar 07:30. Both outside DeepSeek peak
  hours (peak = 08–11 and 13–17 Vietnam time, weekdays).
- A Hermes skill/context file describes DevRadar: what the tools do, the message
  formats, the grounding rule, and "be brief".

### 5.7 devradar MCP server (tools)

`list_projects()`, `get_project(name)`, `history(project, days)`, `get_alerts()`,
`ack_alert(id)`, `snooze(project, days)`, `set_fact(project, key, value)`,
`news_candidates()`, `news_detail(id)`, `news_feedback(id, signal)`.

Read-mostly; writes only to DevRadar's own tables. Stdio transport.

### 5.8 Message formats

- Evening recap: one line per active project + one "Next:" suggestion.
- Morning radar: 5–7 numbered items; "more N" for a deeper dive.
- Alerts: one line with the reason and what to do.

### 5.9 Layout

```
devradar/
  devradar/collector/   # claude_code.py, codex.py, (t3code.py if needed), repo.py
  devradar/state/       # db.py, distiller.py, project_state.py (DeepSeek call)
  devradar/rules/       # one module per rule
  devradar/news/        # sources/, prefilter.py
  devradar/mcp/         # MCP server exposing the tools
  devradar/worker.py    # scheduler loop for the worker process
  hermes/               # context file, cron job definitions, tool trim list
  scripts/              # Windows Task Scheduler install/uninstall (PowerShell)
  config.yaml
  tests/                # fixtures = real (redacted) JSONL samples
```

### 5.10 Cost controls

- No LLM call on polling ticks; the LLM runs only for finished sessions, the two
  daily jobs, alerts and replies.
- Stable prompt prefixes so DeepSeek's automatic cache hits apply.
- The worker logs token usage per call; `/usage` reply in Telegram shows month-to-date
  cost. Soft cap in `config.yaml` (default $6/month): past it, recaps fall back to
  rule-based text and news ranking skips the LLM.

## 6. Decided by the user

- Telegram is the channel.
- Progress signal = agent chat sessions (Claude Code via Orca/CLI; T3 Code under
  evaluation), not commits or sites.
- Progress messages: track progress, notify, push when something seems off.
- News: new trends and tools, engineering-heavy, research-light.
- Runs on the laptop only (Windows).
- Hermes Agent with DeepSeek Flash.

## 7. Calls made by me (check these)

- Cadence: recap 21:30, radar 07:30, collector every 20 min.
- Rule set and thresholds in §5.4; 3-day cooldowns.
- Project-state updates call DeepSeek directly instead of going through Hermes.
- Sessions grouped by repo across Orca worktrees.
- Messages in English, Vietnamese fine in replies.
- Source list in §5.5; 5–7 news items per day.
- $6/month soft cap with graceful fallback.

## 8. Left to the builder

- Exact JSONL field mapping for Claude Code and Codex (build from real samples).
- Error-signature normalization for the Loop rule.
- Prompt wording (subject to the grounding rule).
- How the worker triggers Hermes for alerts (on-demand cron run vs. gateway API),
  whichever the installed Hermes version supports.
- Exact Hermes tool names to disable.
