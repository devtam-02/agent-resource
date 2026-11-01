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

## Install

With `dev-cli`:

```bash
dev-cli --agent-resource init        # pull, then install or update everything
dev-cli --agent-resource status      # what is installed
```

`dev-cli` builds a local marketplace from `plugins/` and installs each plugin with
`claude plugin install`; no version bump is needed after an edit.

Without `dev-cli`, try a plugin for one session:

```bash
claude --plugin-dir plugins/spring-boot-optimizer
```

## Editing

Edit, commit, push. On every machine: `dev-cli --agent-resource init`. Changes take effect in a
new Claude Code session. Keep executable scripts marked in git
(`git update-index --chmod=+x <file>`): the working copy may live on a file system that does
not keep the bit.

This repository is public: no internal host names, credentials, customer data or company code.
