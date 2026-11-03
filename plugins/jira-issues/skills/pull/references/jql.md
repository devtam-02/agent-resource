# JQL cheat sheet (Jira Server / Data Center)

Use with `jira_pull.py pull --jql '<JQL>'` when no preset fits. Quote text values with `"`, escape
`"` and `\` inside them. Keys (`APP`, `APP-123`) and functions need no quotes.

| Need | JQL |
|---|---|
| My open bugs of a project | `project = "APP" AND assignee = currentUser() AND type = Bug AND statusCategory != Done` |
| Open issues of a person | `assignee = "username" AND statusCategory != Done ORDER BY priority DESC` |
| Unassigned in the active sprint | `project = "APP" AND sprint in openSprints() AND assignee is EMPTY` |
| A named sprint | `sprint = "APP Sprint 42"` |
| Issues of an epic | `"Epic Link" = APP-100` (classic Jira Software) or `parent = APP-100` |
| Subtasks of an issue | `parent = APP-412` |
| Fix version | `fixVersion = "2.4.0"` or `fixVersion in unreleasedVersions()` |
| Created / resolved in a window | `created >= -14d`, `resolved >= startOfWeek()`, `updated >= "2026-11-01"` |
| Due this week | `duedate >= startOfWeek() AND duedate <= endOfWeek() AND statusCategory != Done` |
| Work I logged in a date range | `worklogAuthor = currentUser() AND worklogDate >= "2026-11-01" AND worklogDate <= "2026-11-07"` |
| Text search | `text ~ "payment timeout"`, `summary ~ "export"` |
| Label / component | `labels = auth`, `component = "web"` |
| Status by name | `status in ("In Progress", "In Review")` |
| Status changed | `status changed to Done during (-7d, now())` |
| Watched by me | `watcher = currentUser()` |

- `statusCategory` is `"To Do"`, `"In Progress"`, `Done`. It is better than status names because
  every workflow has its own names.
- `ORDER BY` goes last: `ORDER BY priority DESC, updated DESC`.
- dev-cli stops at 500 issues (`truncated: true`). Narrow the query rather than paging.
- Field names differ between instances (`"Epic Link"`, custom fields). When Jira answers 400, read
  its message, fix the query, and retry once; do not guess repeatedly.
- `worklogDate` and the work log of other people need the Jira permission to see work logs.
