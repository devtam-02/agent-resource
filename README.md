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
| [`app-config-review`](plugins/app-config-review/) | Read-only review of a service's staging configuration kept in Git config repositories: plain-text secrets, risky settings, staging pointing at production, drift against UAT/production. |

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
| `app-config-review` | `APP_CONFIG_STAGING_REPO`, `APP_CONFIG_REPO`, `APP_CONFIG_BRANCH`, `APP_CONFIG_CACHE` |
| `spring-boot-optimizer` | `SPRING_BOOT_OPTIMIZER_HOOK` (`off` silences the hook) |
| `canva-slides` | none (Canva login is Claude Code's OAuth, `/mcp`) |

New plugin settings: read them with the same small reader (copy `setting()` from
`plugins/app-config-review/skills/review/scripts/config_review.py`), add the keys to
dev-cli's `config/.env.example` and to the table above. Secrets never go in a plugin file.

## Editing

Edit, commit, push. On every machine: `dev-cli --agent-resource init`. Changes take effect in a
new Claude Code session. Keep executable scripts marked in git
(`git update-index --chmod=+x <file>`): the working copy may live on a file system that does
not keep the bit.

This repository is public: no internal host names, credentials, customer data or company code.
