# jira-issues

Claude Code plugin that pulls issues from Jira Server / Data Center through
[dev-cli](#requirements) (`dev-cli --jira-tool scan-issues`). Read-only. It has skills only, no
legacy `commands/`.

| Skill | What it does |
|---|---|
| `pull`, invoked as `/jira-issues:pull [preset\|keys\|"JQL"] [--detail] [--save file]` | Turns the request into one filter: a preset, issue keys, or JQL (`references/jql.md`). It runs dev-cli, keeps the JSON in a private cache and answers from a compact digest: status groups, flags (overdue, due soon, unassigned, stale, over estimate) and work log. It can show one issue in full with its description, subtasks and last comments. On request it saves a JSON or Markdown file. Claude also loads it on its own for Jira questions. |

## Setup

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
python3 skills/pull/scripts/jira_pull.py doctor
```

## Script

```bash
J=skills/pull/scripts/jira_pull.py
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

## Requirements

- `python3`.
- `dev-cli` with its jira-tool, on `PATH`, or set `JIRA_ISSUES_DEV_CLI`.
- A Jira Server / Data Center account whose Personal Access Token can read the issues.
- Jira Cloud is not supported by dev-cli.
