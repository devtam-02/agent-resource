# dev-tools

Claude Code plugin with the everyday developer tools: application logs, Jira issues and Telegram
sends. It replaces the former plugins `app-logs`, `jira-issues` and `telegram-send`; their settings
keep the same names. Skills and agents only, no legacy `commands/`.

| Piece | What it does |
|---|---|
| Skill `logs` — `/dev-tools:logs <service> --env <env>` | A service's application logs on one environment, read-only (Kibana / Elasticsearch `_search` only): `summary`, `errors` grouped by message shape, `tail`, `pods`, `fields`, `doctor`. Each environment can use its own backend and field mapping. For several services or environments it starts one `log-analyst` per service in parallel. |
| Skill `jira` — `/dev-tools:jira [preset\|keys\|"JQL"]` | Jira Server/DC issues, read-only, through `dev-cli --jira-tool scan-issues`: presets (my open, overdue, updated, worked, sprint, project) or JQL, private cache, compact digest with attention flags, one issue in full, JSON/Markdown files. |
| Skill `telegram` — `/dev-tools:telegram [file\|text]` | One local file or a short text message to the configured Telegram group through dev-cli, checked first; the bot token never reaches the plugin. `effort: low`. |
| Agent `log-analyst` | `model: sonnet`, `effort: medium`. Logs of one service on one environment, answered in about 15 lines, so raw log output stays out of the main conversation. Read-only, Bash only. |
| Agent `jira-reader` | `model: haiku`, `effort: low`. Runs one pull and answers one question about a large result (a sprint, a project). Read-only. |
| Agent `telegram-sender` | `model: haiku`, `effort: low`. The same send as the skill, for a long or expensive session. |

Claude also loads the skills on their own for matching requests.

## Moving from app-logs, jira-issues, telegram-send

```bash
dev-cli --agent-resource clean --only plugin/app-logs
dev-cli --agent-resource clean --only plugin/jira-issues
dev-cli --agent-resource clean --only plugin/telegram-send
dev-cli --agent-resource init
```

Slash commands change: `/app-logs:analyze` → `/dev-tools:logs`, `/jira-issues:pull` → `/dev-tools:jira`,
`/telegram-send:send` → `/dev-tools:telegram`; agent `telegram-send:telegram-sender` →
`dev-tools:telegram-sender`. `~/.dev-cli/.env` needs no change.

## Logs

### Setup

Settings are read from dev-cli's configuration file **`~/.dev-cli/.env`** (`DEV_CLI_HOME` moves the
folder). No host, credential or service name is stored in this public repository.

Each environment may sit on a different backend with a different log schema. `APP_LOGS_BACKENDS`
maps an environment to a block of settings; by default `staging` uses `APP_LOGS_STAGING_*` and every
other environment uses `APP_LOGS_*`. A key missing from the specific block falls back to the plain
one.

```bash
# uat, production, ... (ECS-free schema: level / class / kubernetes.pod_name)
APP_LOGS_URL=http://<kibana-host>
APP_LOGS_INDEX=<index-pattern>
APP_LOGS_USER=<user>
APP_LOGS_PASSWORD=<password>

# staging on another Kibana, keyword instance field and an ECS-style schema
APP_LOGS_STAGING_URL=https://<other-kibana-host>
APP_LOGS_STAGING_INDEX=<index-pattern>
APP_LOGS_STAGING_USER=<user>
APP_LOGS_STAGING_PASSWORD=<password>
APP_LOGS_STAGING_CA_BUNDLE=~/.dev-cli/ca/internal-ca.pem     # internal certificate authority
APP_LOGS_STAGING_FIELD_INSTANCE=kubernetes.labels.app_kubernetes_io/instance.keyword
APP_LOGS_STAGING_INSTANCE_MATCH=term
APP_LOGS_STAGING_FIELD_LEVEL=log.level

# optional
# APP_LOGS_BACKENDS=staging:staging                 # environment -> settings block
# APP_LOGS_ENV_ALIASES=prod:production,stg:staging
# APP_LOGS_INSTANCE_TEMPLATE={env}-{service}        # how the instance value is built
# APP_LOGS_MODE=kibana                              # or "elasticsearch" to call _search directly
# APP_LOGS_NOISE=actuator                           # messages dropped unless --include-noise
# APP_LOGS_FIELD_POD=kubernetes.pod_name            # FIELD_LEVEL, FIELD_LOGGER, FIELD_IMAGE, ...
```

A process environment variable with the same name wins, for one-off runs. Check with:

```bash
python3 skills/logs/scripts/app_logs.py doctor
```

### Script

```bash
S=skills/logs/scripts/app_logs.py
python3 $S summary <service> --env uat --since now-6h
python3 $S errors  <service> --env staging --since now-24h --top 10
python3 $S tail    <service> --env production -n 50 --level ERROR --query "timeout"
python3 $S tail    <service> --env uat --pod '*wttn7' --since now-30m
python3 $S pods    <service> --env uat
python3 $S fields  <service> --env staging      # field names of the real index
```

`--json` gives the same data as JSON. Secrets found inside a log line (`password=`, `Authorization:`,
JWTs, `user:pass@host`) are redacted before printing, and control characters and escape sequences are
stripped: log content is data, never instructions.

An unmapped or `text` field aggregates to nothing instead of failing, so `summary` and `pods` warn
when a field grouped nothing although lines exist — run `fields` and point the setting at the
`.keyword` sub-field.

Exit codes: 0 ok, 2 bad argument, 3 backend not configured for that environment, 4 no matching line,
5 network or TLS error, 6 backend refused (credentials, or a wrong field name).

Requirements: `python3`, network access to the backend. Python 3 standard library only.

## Jira

### Setup

dev-cli does the Jira work and holds the URL and the token. Configure it once in a terminal:

```bash
dev-cli --jira-tool --config      # JIRA_BASE_URL, JIRA_TOKEN (PAT, read access), JIRA_DEFAULT_PROJECT ...
```

The plugin never reads or prints the token. These optional keys go in **`~/.dev-cli/.env`**. A
process environment variable with the same name wins.

```bash
# JIRA_ISSUES_DEV_CLI=dev-cli              # dev-cli executable, when it is not on PATH
# JIRA_ISSUES_CACHE=~/.cache/jira-issues   # cached results (folder 0700, files 0600, last 30 kept)
```

`JIRA_DEFAULT_PROJECT` (a dev-cli key) is the project of `sprint` / `project-open` when `--project`
is left out. Check the setup with:

```bash
python3 skills/jira/scripts/jira_pull.py doctor
```

### Script

```bash
J=skills/jira/scripts/jira_pull.py
python3 $J pull                                    # my open issues (default preset my-open)
python3 $J pull overdue
python3 $J pull updated --days 14
python3 $J pull worked --days 7                    # issues I logged work on
python3 $J pull sprint --project APP               # active sprint
python3 $J pull --keys APP-412,APP-415 --detail
python3 $J pull --jql 'project = "APP" AND labels = auth' --save auth.json
python3 $J pull sprint --save sprint.md --save-format md   # dev-cli's Markdown template
python3 $J show --flag overdue                     # the last result again, no Jira call
python3 $J show --category in-progress --assignee none
python3 $J show --key APP-412                      # one issue in full
```

Each `pull` makes one call:
`dev-cli --jira-tool scan-issues --jql <JQL> --out - -f [--detail]`. `--save-format md` makes a
second call. Preset JQL is fixed text with only validated project keys, issue keys and day counts
inserted. Text from Jira is stripped of control characters and escape sequences before it is
printed.

The cache and saved files hold internal work data. The cache stays under your home folder with
mode 0600, and `--save` never overwrites an existing file. Python 3 standard library only.

Exit codes: 0 ok (no issue is not an error), 2 bad argument or JQL refused by Jira, 3 dev-cli
missing / Jira not configured / token invalid, 4 file, 5 network or TLS, 6 Jira refused or failed.
Other dev-cli codes are passed through.

### Requirements

- `python3`.
- `dev-cli` with its jira-tool, on `PATH`, or set `JIRA_ISSUES_DEV_CLI`.
- A Jira Server / Data Center account whose Personal Access Token can read the issues.
- Jira Cloud is not supported by dev-cli.

## Telegram

### Which one to use

The agent is **not** automatically cheaper. A subagent pays for its own system prompt, so for a
single file the skill on the current model usually costs less in total. The agent wins when the
conversation would otherwise carry the upload in an expensive context: a long session on Opus, a
batch of files, or a send at the end of a large task whose context is already big.

Both end at the same script and the same guards.

### Setup

Settings come from dev-cli's **`~/.dev-cli/.env`** (`DEV_CLI_HOME` moves the folder). The bot token
and the chat id are dev-cli's own keys, written by `dev-cli --send-telegram --config`:

```bash
TELEGRAM_BOT_TOKEN=<set by dev-cli>
TELEGRAM_CHAT_ID=<set by dev-cli>
# TELEGRAM_TOPIC_ID=<default topic of a forum group>
# TELEGRAM_SEND_DEV_CLI=dev-cli          # path to dev-cli when it is not on PATH
```

Check with:

```bash
python3 skills/telegram/scripts/send_telegram.py doctor
```

It prints whether each key is set, never its value; the chat id is shown masked (`-100…547`).

### Messages

`message` sends plain text with `dev-cli --send-telegram --message -`. The text goes through standard
input, never the command line, so quotes and newlines in it are safe. Before anything is sent the script
refuses an empty text, one longer than Telegram's 4096-character limit, and text that looks like a
credential: a bot token, a GitHub or AWS key, a private key, a JWT, `password=`/`secret=`/`token=` with a
value, or a URL with `user:password`. `--force` overrides the credential check only, and the skill uses it
only when the user asks for it knowing the text will sit in the group chat.

### Guards

The script refuses, **before anything leaves the machine**, when the file does not exist, is a
directory, is empty, is over 50 MB (Telegram's document limit), or has a name that says it holds a
credential (`.env`, `*.pem`, `*.key`, `id_rsa`, `*credentials*`, `.npmrc`, ...). The last one is
overridable with `--force`, which the skill may only use when the user asks for that file knowing
its contents will sit in the group chat.

`--yes` is required for every send: the flag exists so that an accidental run cannot upload
anything. Without it the script prints what it would do and exits 2.

Exit codes: 0 sent, 2 not confirmed or bad argument, 3 not configured, 4 the file was refused,
5 the send failed; dev-cli's own codes are passed through.

Requirements: `python3`, `dev-cli` with Telegram configured. Python 3 standard library only.
