# agent-resource

Personal Claude Code resources, installed on a machine by
[`dev-cli --agent-resource init`](#install) (pulls this repository, then installs).

```text
plugins/<name>/     Claude Code plugins (skills, agents, hooks; no legacy commands/)
rules/*.md          rules, copied to ~/.claude/rules/dev-cli/
memories/*.md       CLAUDE.md content, imported from ~/.claude/CLAUDE.md through one @import line
```

## Plugins

| Plugin | Purpose |
|---|---|
| [`spring-boot-optimizer`](plugins/spring-boot-optimizer/) | Review and optimize Java / Spring Boot services: Sonar self-check, money and transaction safety, performance, hexagonal style, test quality. |
| [`canva-slides`](plugins/canva-slides/) | Presentations: checked Markdown outline with speaker notes (from a topic, specs, a Jira export or a git range), then a Canva deck through Canva's MCP server, then PDF/PPTX export. |
| [`app-config-review`](plugins/app-config-review/) | Service configuration in Git config repositories, read-only. Skill `app-config-access` reads it (secrets masked); skill `app-config-review`, run in a code repository, reviews an environment's config against the matching code branch (staging → `staging`, uat → `release`, else current code or a named branch). |
| [`jira-issues`](plugins/jira-issues/) | Jira Server/DC issues, read-only, through `dev-cli --jira-tool scan-issues`: presets (my open, overdue, updated, worked, sprint, project) or JQL, cached JSON, compact digest with attention flags, one issue in full, JSON/Markdown files. |
| [`app-logs`](plugins/app-logs/) | Application logs of a service on one environment (Kibana / Elasticsearch), read-only: summary of levels, pods, timeline and top errors, errors grouped by message shape, recent lines, pods with their running image. Each environment can use its own backend and field mapping. |
| [`spring-pr-review`](plugins/spring-pr-review/) | Reviews a Spring Boot pull request from the local git range (read-only scan `pr_scan.py`): what each commit does, impact, config keys and migrations added/changed/removed, code findings. Report from a fixed template, Markdown file, optional Telegram summary (only when asked). |
| [`telegram-send`](plugins/telegram-send/) | Sends one local file to the configured Telegram group through dev-cli (the bot token never reaches the plugin), with the file checked first. Skill `send` on low effort; agent `telegram-sender` runs the same send on `model: haiku`. |

## Install

With `dev-cli`:

```bash
dev-cli --agent-resource init        # pull, then install or update everything
dev-cli --agent-resource status      # what is installed
```

`dev-cli` builds a local marketplace from `plugins/` and installs each plugin with
`claude plugin install`; no version bump is needed after an edit.

Plugins that declare a remote MCP server (`canva-slides`) need a one-time login: `/mcp` →
the server → Authenticate. Tokens stay in Claude Code, never in this repository.

Without `dev-cli`, try a plugin for one session:

```bash
claude --plugin-dir plugins/spring-boot-optimizer
```

## Settings

Plugins never keep settings in this repository. Any script, skill or hook that needs one reads
dev-cli's configuration file **`~/.dev-cli/.env`** (`DEV_CLI_HOME` moves the folder; a process
environment variable with the same name wins). dev-cli creates the file on install, with the
keys of every plugin listed, commented out, in its template. Current keys:

| Plugin | Keys |
|---|---|
| `app-config-review` | `APP_CONFIG_STAGING_REPO`, `APP_CONFIG_REPO`, `APP_CONFIG_BRANCH`, `APP_CONFIG_CACHE`, `APP_CONFIG_CODE_BRANCHES`, `APP_CONFIG_LIBRARY_PREFIXES` |
| `spring-boot-optimizer` | `SPRING_BOOT_OPTIMIZER_HOOK` (`off` silences the hook) |
| `canva-slides` | none (Canva login is Claude Code's OAuth, `/mcp`) |
| `jira-issues` | `JIRA_ISSUES_DEV_CLI`, `JIRA_ISSUES_CACHE`; reads `JIRA_DEFAULT_PROJECT`. Jira URL and token belong to dev-cli (`dev-cli --jira-tool --config`) |
| `app-logs` | `APP_LOGS_URL`, `APP_LOGS_INDEX`, `APP_LOGS_USER`, `APP_LOGS_PASSWORD`, `APP_LOGS_CA_BUNDLE`, `APP_LOGS_MODE`, `APP_LOGS_BACKENDS`, `APP_LOGS_ENV_ALIASES`, `APP_LOGS_INSTANCE_TEMPLATE`, `APP_LOGS_INSTANCE_MATCH`, `APP_LOGS_NOISE`, `APP_LOGS_FIELD_*`; the same keys again per backend (`APP_LOGS_STAGING_*`) |
| `telegram-send` | `TELEGRAM_SEND_DEV_CLI`; reads dev-cli's own `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `TELEGRAM_TOPIC_ID` (`dev-cli --send-telegram --config`) |

New plugin settings: read them with the same small reader (copy `setting()` from
`plugins/app-config-review/skills/app-config-access/scripts/app_config.py`), add the keys to
dev-cli's `config/.env.example` and to the table above. Secrets never go in a plugin file.

## Editing

Edit, commit, push. On every machine: `dev-cli --agent-resource init`. Changes take effect in a
new Claude Code session. Keep executable scripts marked in git
(`git update-index --chmod=+x <file>`): the working copy may live on a file system that does
not keep the bit.

This repository is public: no internal host names, credentials, customer data or company code.
