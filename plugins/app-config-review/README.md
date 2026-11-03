# app-config-review

Claude Code plugin that reviews the **staging** configuration of a service kept in Git config
repositories, read-only. Skills only, no legacy `commands/`.

Expected layout, branch `master`:

```text
<staging repo>/<service>/staging/...        staging
<main repo>/<service>/uat/...               UAT
<main repo>/<service>/production/...        production
```

| Component | What it does |
|---|---|
| skill `review` — `/app-config-review:review <service...> [--compare uat,production]` or `--list [text]` | Runs `config_review.py`, checks each finding against the real file, and reports by severity: plain-text secrets, secrets shared with UAT/production, staging pointing at production, risky settings (`ddl-auto`, actuator, auth off, TLS checks off, `show-sql`, timeouts), and drift against UAT/production. Claude also loads it on its own for config review requests. Reference: `rules.md` (what each rule means and its usual false positives). |

## Setup

Settings are read from dev-cli's configuration file **`~/.dev-cli/.env`** (created by
`dev-cli`'s `install.sh` or its first run; `DEV_CLI_HOME` moves the folder). The repository
addresses are **not** stored in this public repository. Add to that file:

```bash
APP_CONFIG_STAGING_REPO=https://<host>/scm/<project>/<staging-config-repo>.git
APP_CONFIG_REPO=https://<host>/scm/<project>/<main-config-repo>.git
# APP_CONFIG_BRANCH=master                      # default
# APP_CONFIG_CACHE=~/.cache/app-config-review   # default
```

A process environment variable with the same name wins, for one-off runs. Use `ssh://` /
`git@` URLs or a git credential helper for access; a URL with a user or password in it is
refused. Check with:

```bash
python3 skills/review/scripts/config_review.py doctor
```

## Script

```bash
S=skills/review/scripts/config_review.py
python3 $S list --filter <text>           # services that have a staging/ folder
python3 $S review <service>               # text report, exit 1 when there are errors
python3 $S review <service> --json        # same, as JSON
python3 $S review <service> --compare ''  # staging only
```

Repositories are cloned shallow into `~/.cache/app-config-review/` (override with
`APP_CONFIG_CACHE`) and refreshed on each run (`--no-fetch` to skip). Nothing is committed or
pushed. Secret values are never printed: findings give the key and `file:line`. Python 3 standard
library and `git` only.

Exit codes: 0 clean, 1 errors found, 2 bad argument, 3 not configured, 4 service not found, 5 git/network.

Requirements: `python3`, `git`, read access to both repositories.
