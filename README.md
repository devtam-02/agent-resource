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
| [`code-review`](plugins/code-review/) | Reviews code and git history before it ships, for Spring Boot services and front-end apps (React/Next.js, Vue/Nuxt, Angular, Svelte). Skill `review-pr`: a pull request or branch range (what each commit does, impact, config and migrations against each environment, Kafka, front-end routes, API calls, env variables and security rules) plus a deep line-by-line review by the `code-reviewer` agent on `model: opus`; reads Bitbucket Server/DC when configured. Skill `compare`: before a merge, the missing commits and what the merge really changes. Skill `changes`: what changed between two revisions of one or more services, written into an Obsidian vault (`service-changes` agents on Sonnet/Opus, `vault-publisher` on Haiku). Formerly `spring-pr-review` and `branch-compare`. |
| [`dev-tools`](plugins/dev-tools/) | Everyday developer tools. Skill `logs`: a service's application logs on one environment (Kibana / Elasticsearch, read-only), summary, errors grouped by message shape, recent lines, pods; agent `log-analyst` (Sonnet) per service in parallel. Skill `jira`: Jira Server/DC issues, read-only, through `dev-cli --jira-tool scan-issues`, cached digest with attention flags; agent `jira-reader` (Haiku). Skill `telegram`: one file or a short message to the configured Telegram group through dev-cli, checked first; agent `telegram-sender` (Haiku). Skill `sql-runner`: SQL through a web SQL Runner tool's REST API, read-only by default, writes only after confirmation; agent `sql-explorer` (Sonnet). Formerly `app-logs`, `jira-issues` and `telegram-send`. |
| [`obsidian-vault`](plugins/obsidian-vault/) | An Obsidian vault without Obsidian open: skill `read` (search by text, tag, property; a note, a section, outline, links, backlinks), skill `write` (create, append under a heading, daily note, properties; never overwrites unless asked, never touches `.obsidian/`), skill `sync` (Git Vault Sync through the Obsidian CLI, waits for commit and push; status and conflicts). Agents `vault-reader` and `vault-syncer` on Haiku. |

## Rules

| Rule | Purpose |
|---|---|
| [`db-write-safety`](rules/db-write-safety.md) | Never write to a database on your own; every write (SQL, DDL, Mongo/Redis writes, migrations) is shown, approved, confirmed with a code typed back by the user, then approved again in Claude Code's prompt (dev-tools hook). |

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

Moving from a plugin that was merged into another (`app-logs`, `jira-issues`, `telegram-send` →
`dev-tools`; `spring-pr-review`, `branch-compare` → `code-review`): remove the old one, then install.

```bash
dev-cli --agent-resource clean --only plugin/app-logs     # once per old plugin
dev-cli --agent-resource init
```

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
| `code-review` | `BITBUCKET_URL`, `BITBUCKET_TOKEN` (or `BITBUCKET_USER` + `BITBUCKET_PASSWORD`), `BITBUCKET_CA_BUNDLE`, `SPRING_PR_REVIEW_DIALECT`, `SPRING_PR_REVIEW_APP_CONFIG`, `OBSIDIAN_VAULT`, `BRANCH_COMPARE_OBSIDIAN_DIR` (default `code-changes`), `BRANCH_COMPARE_OBSIDIAN_SYNC`, `BRANCH_COMPARE_OBSIDIAN_SYNC_COMMAND` (default `OBSIDIAN_SYNC_COMMAND`); uses obsidian-vault's `OBSIDIAN_CLI`, `OBSIDIAN_VAULT_NAME` and app-config-review's `APP_CONFIG_*` (names kept from the former plugins) |
| `dev-tools` | logs: `APP_LOGS_URL`, `APP_LOGS_INDEX`, `APP_LOGS_USER`, `APP_LOGS_PASSWORD`, `APP_LOGS_CA_BUNDLE`, `APP_LOGS_MODE`, `APP_LOGS_BACKENDS`, `APP_LOGS_ENV_ALIASES`, `APP_LOGS_INSTANCE_TEMPLATE`, `APP_LOGS_INSTANCE_MATCH`, `APP_LOGS_NOISE`, `APP_LOGS_FIELD_*`, the same keys again per backend (`APP_LOGS_STAGING_*`); jira: `JIRA_ISSUES_DEV_CLI`, `JIRA_ISSUES_CACHE`, reads `JIRA_DEFAULT_PROJECT` (Jira URL and token belong to dev-cli, `dev-cli --jira-tool --config`); telegram: `TELEGRAM_SEND_DEV_CLI`, reads dev-cli's own `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `TELEGRAM_TOPIC_ID` (`dev-cli --send-telegram --config`); sql-runner: `SQL_RUNNER_URL`, `SQL_RUNNER_USER`, `SQL_RUNNER_PASSWORD`, `SQL_RUNNER_API_PATH`, `SQL_RUNNER_CA_BUNDLE`, `SQL_RUNNER_TIMEOUT`, `SQL_RUNNER_CONNECTION`, `SQL_RUNNER_WRITE_CONNECTIONS`, `SQL_RUNNER_PROD_PATTERN`. Names kept from the former plugins |
| `obsidian-vault` | `OBSIDIAN_VAULT`, `OBSIDIAN_VAULT_NAME`, `OBSIDIAN_INBOX`, `OBSIDIAN_CLI`, `OBSIDIAN_SYNC_COMMAND` (default `git-vault-sync:sync`), `OBSIDIAN_SYNC_TIMEOUT` (default 90), `OBSIDIAN_AUTO_SYNC` (default off), `OBSIDIAN_BACKUP_DIR` |

New plugin settings: read them with the same small reader (copy `setting()` from
`plugins/app-config-review/skills/app-config-access/scripts/app_config.py`), add the keys to
dev-cli's `config/.env.example` and to the table above. Secrets never go in a plugin file.

## Editing

Edit, commit, push. On every machine: `dev-cli --agent-resource init`. Changes take effect in a
new Claude Code session. Keep executable scripts marked in git
(`git update-index --chmod=+x <file>`): the working copy may live on a file system that does
not keep the bit.

This repository is public: no internal host names, credentials, customer data or company code.
