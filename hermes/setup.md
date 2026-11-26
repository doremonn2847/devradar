# Hermes setup for DevRadar (Windows)

Hermes keeps its files in `%LOCALAPPDATA%\hermes\` (its `HERMES_HOME`). Commands below are
from the Hermes docs; if one differs on your installed version, check `hermes <command> --help`.

## 1. Install Hermes

```powershell
iex (irm https://raw.githubusercontent.com/NousResearch/hermes-agent/main/scripts/install.ps1)
```

## 2. Model: DeepSeek Flash

DeepSeek is a built-in provider. Put your key in `%LOCALAPPDATA%\hermes\.env`:

```
DEEPSEEK_API_KEY=sk-...
```

Then run `hermes model`, choose **DeepSeek**, and enter model `deepseek-flash`.
Result in `%LOCALAPPDATA%\hermes\config.yaml` should look like:

```yaml
model:
  provider: deepseek
  default: deepseek-flash
```

## 3. Telegram

1. In Telegram, talk to @BotFather → `/newbot` → copy the token.
2. Talk to @userinfobot → copy your numeric user id.
3. Run `hermes gateway setup`, pick Telegram, paste the token, and set the allowed user
   to your id only (`TELEGRAM_ALLOWED_USERS=<your id>`).

## 4. Connect the devradar tools (MCP)

Add to `config.yaml` (use the full path that `where devradar-mcp` prints):

```yaml
mcp_servers:
  devradar:
    command: "C:\\Users\\<you>\\AppData\\Local\\Programs\\Python\\Python312\\Scripts\\devradar-mcp.exe"
    args: []
```

Then in a Hermes chat run `/reload-mcp`, and ask "what projects do you see?".

## 5. Trim tools (cost + safety)

Every call carries all enabled tool definitions, so fewer tools = cheaper calls. And since
anyone holding your phone can talk to this bot, it should not have a shell on your laptop.

```yaml
disabled_toolsets: [browser, image_gen, tts, code_execution, terminal, file]
```

Keep: `memory`, `cronjob`, `web`, and the `devradar` MCP server. Check with `/tools list`.

## 6. Skill

Copy `hermes\skills\devradar\` from this repo to `%LOCALAPPDATA%\hermes\skills\devradar\`.

## 7. Cron jobs

```powershell
hermes cron create "every day at 21:30" "Send the DevRadar evening recap." --skill devradar --deliver telegram --name devradar-recap
hermes cron create "every day at 07:30" "Send the DevRadar morning radar." --skill devradar --deliver telegram --name devradar-radar
hermes cron create "every 24h" "Report pending DevRadar alerts, then ack them. Send nothing if there are none." --skill devradar --deliver telegram --name devradar-alerts --paused --paused-reason "run on demand by the devradar worker"
hermes cron list
```

`devradar-alerts` stays paused: the worker runs it with `hermes cron run devradar-alerts`
only when a new alert exists (paused jobs still run on demand). Check that `hermes cron list`
shows times in Asia/Bangkok; if not, set Hermes's timezone or adjust the times.

## 8. Start the gateway at logon

```powershell
hermes gateway install
```

## 9. Test

```powershell
hermes cron run devradar-recap   # recap should arrive on Telegram within a minute
```
