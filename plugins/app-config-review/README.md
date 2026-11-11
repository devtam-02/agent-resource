# app-config-review

Claude Code plugin for service configuration kept in Git config repositories, read-only. Skills
only, no legacy `commands/`.

Expected layout, branch `master` (`APP_CONFIG_BRANCH`):

```text
<staging repo>/<service>/staging/...          staging
<main repo>/<service>/<environment>/...       uat, production, and every other environment
```

| Skill | What it does |
|---|---|
| `app-config-access` — `/app-config-review:app-config-access` | Reads the config repositories: services, environments of a service, the config of a service for one environment with secrets masked, the local folder of it. Script `skills/app-config-access/scripts/app_config.py`, also the library the review uses. |
| `app-config-review` — `/app-config-review:app-config-review --env <env> [--code current\|<branch>]` | Run inside a code repository. Reviews the config of `<env>` against the code that will run with it: `staging` → branch `staging`, `uat` → branch `release`, any other environment → `--code current` (working tree, uncommitted changes included) or `--code <branch>`. It answers two questions first — **which keys are missing** and **what stops the application from starting** — then reports wrong value types, unresolved placeholders, keys nothing reads (possibly renamed), plain-text and shared secrets, secrets committed in the code repository, risky settings, other-environment addresses, and drift with `--compare`. The result comes in three tables: missing config, wrong or suspect config, what to do, saved as a Markdown report (`--md review-reports/config-review-<service>-<env>-<date>.md` writes a draft; the skill confirms each finding and edits it into the final report). References: `references/rules.md`, `references/report-template.md`. |

Claude also loads either skill on its own for matching requests.

## Setup

Settings are read from dev-cli's configuration file **`~/.dev-cli/.env`** (created by
`dev-cli`'s `install.sh` or its first run; `DEV_CLI_HOME` moves the folder). The repository
addresses are **not** stored in this public repository. Add to that file:

```bash
APP_CONFIG_STAGING_REPO=https://<host>/scm/<project>/<staging-config-repo>.git
APP_CONFIG_REPO=https://<host>/scm/<project>/<main-config-repo>.git
# APP_CONFIG_BRANCH=master                          # branch of both config repositories
# APP_CONFIG_CACHE=~/.cache/app-config-review       # shallow clones
# APP_CONFIG_CODE_BRANCHES=staging:staging,uat:release   # environment -> code branch
# APP_CONFIG_LIBRARY_PREFIXES=mylib,app.common      # keys read by shared libraries, not "unused"
```

A process environment variable with the same name wins, for one-off runs. Use `ssh://` /
`git@` URLs or a git credential helper for access; a URL with a user or password in it is
refused. Check with:

```bash
python3 skills/app-config-access/scripts/app_config.py doctor
```

## Scripts

```bash
A=skills/app-config-access/scripts/app_config.py
python3 $A list --env uat --filter <text>         # services that have a uat/ folder
python3 $A envs <service>                         # its environments and their repository
python3 $A show <service> --env staging           # key=value, secrets as <secret>

R=skills/app-config-review/scripts/config_review.py   # run from the code repository
python3 $R --env staging                          # code: origin/staging
python3 $R --env uat --compare production --json  # code: origin/release
python3 $R --env production --code master
python3 $R --env staging --code current           # what is on disk now
python3 $R --env staging --no-code --service <s>  # config rules only
```

`config_review.py` prints three tables — missing config, wrong or suspect config, what to do — with
a start-up verdict above them; every finding carries `startup` (`yes`, `maybe` or empty) and the rows
of the tables are in `sections` with `--json`. Findings that are not something to change (code
defaults in use, keys nothing reads, drift details) are listed as notes below the tables.

The code branch is fetched (`git fetch origin <branch>`) and read from git objects: the working
tree and the checked-out branch are never touched. The config repositories are cloned shallow into
the cache and refreshed on each run (`--no-fetch` to skip). Nothing is committed or pushed. Secret
values are never printed: findings give the key and `file:line`. Python 3 standard library and
`git` only.

Exit codes: 0 no error, 1 errors found, 2 bad argument (or no code branch for that environment),
3 not configured, 4 service/branch not found, 5 git/network.

Requirements: `python3`, `git`, read access to the config repositories and the code repository.
