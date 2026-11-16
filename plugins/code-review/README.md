# code-review

Three skills and three agents to review code and git history before it ships, read-only on the
repositories. Formerly two plugins, `spring-pr-review` (review) and `branch-compare` (compare, changes),
merged so that their scripts exist once.

| Skill | Question | Report |
|---|---|---|
| `code-review:review-pr` | Is this pull request (or branch range) safe, and what is wrong in its code? Spring Boot and front-end. | `review-reports/<name>.md` in the repository |
| `code-review:compare` | Before merging one branch into another (default staging into release): which commits are missing, what the merge really changes, what to do before and after. | `review-reports/<name>.md` |
| `code-review:changes` | What changed between two tags, branches or commits of one or more services: business flows to accept, config, API, database, Kafka. | Obsidian vault |

| Agent | Model | Job |
|---|---|---|
| `code-reviewer` | opus, effort medium | reads the changed files line by line with their callers and reports real defects (review-pr) |
| `service-changes` | sonnet, effort high (opus for very large ranges) | one service's change report (changes) |
| `vault-publisher` | haiku | writes the reports into the vault and runs its sync (changes) |

```text
scripts/
  pr_scan.py                     git range scan: categories (Spring, front-end), rules, trial merge, review files
  bitbucket_pr.py                Bitbucket PR, GET only: title, branches, commits, merge status, CI builds
  env_overrides.py               config keys x staging/uat/production overrides (via app-config-review)
  branch_compare.py              commit comparison before a merge, trial merge, release checks
  ref_changes.py                 changes between two revisions; --discover, --cross
  java_inventory.py              Java reader: endpoints, Feign calls, DTO fields, entities, enums, jobs, listeners
  obsidian_note.py               writes notes and the run summary into the vault, runs its sync
  plugin_settings.py             settings reader (~/.dev-cli/.env)
skills/review-pr/                SKILL.md, references/report-template.md, references/checklist.md
skills/compare/                  SKILL.md, references/report-template.md, references/checklist.md
skills/changes/                  SKILL.md, references/changes-template.md
agents/                          code-reviewer.md, service-changes.md, vault-publisher.md
tests/                           unittest suite, offline (python3 -m unittest discover -s tests -t tests)
```

## Review a pull request (`review-pr`)

```text
review PR https://<bitbucket>/projects/PROJ/repos/svc/pull-requests/13
review nhánh feature/order vào staging
review code front-end nhánh feature/login vào staging
```

The skill reads the PR (if Bitbucket is configured), fetches both branches, runs `pr_scan.py`, and starts
the `code-reviewer` agent in the background on the changed source files while it compares the changed
config keys with each environment, reads the migrations, config and flagged commits, and the front-end
routes, API calls and environment variables. It checks every 🔴 the agent reports against the code before
writing `review-reports/<report name>.md` from the template: commits, impact, config and database,
front-end, code review, conclusion. Only when asked, it sends that Markdown file to Telegram (telegram-send).

## What the scan reports

- **Range**: commits (merges skipped), files, ±lines, authors and dates, database dialect (from JDBC
  URLs and drivers, or `--dialect`), trial merge result (`git merge-tree --write-tree`), whether build
  files changed, suggested report name (branch names with `/` are made safe).
- **Migrations**:
  - existing migration modified or deleted (high); Liquibase master/index changelogs are recognised and
    only flagged when an existing include changes path (high) or moves;
  - include declared after a higher-numbered file; new change file that no changelog includes;
  - Flyway version duplicated (high) or lower than the target branch's highest;
  - Liquibase change types in YAML, XML or JSON: drop*, rename*, modifyDataType, addNotNullConstraint,
    dropNotNullConstraint, add/dropUniqueConstraint, delete/update, raw SQL; addColumn nullable=false
    without default; no rollback for changes that cannot roll back; partial `dbms` conditions;
  - SQL (comments and quoted strings ignored): DROP, TRUNCATE, RENAME, type change (ALTER COLUMN TYPE,
    MariaDB/MySQL MODIFY and CHANGE), NOT NULL, new UNIQUE, DELETE/UPDATE without WHERE; per database:
    index without CONCURRENTLY (PostgreSQL), generated PERSISTENT/STORED columns and index builds
    (MariaDB/MySQL).
- **Config keys** (by name; YAML nesting is not tracked) added, changed, removed in `application*`,
  `bootstrap*`, `config/`, Helm values and `.env`. Flags: ddl-auto, actuator `include=*`, secret-looking
  keys with a literal value (value never printed), Kafka group id, `auto-offset-reset=earliest` (high
  together with a new group or listener), topic keys, concurrency, timeouts, profiles, open-in-view.
- **Java**: JPA mapping, `@Transactional`, endpoint mapping, EAGER fetch, `permitAll()`/CSRF off,
  `@KafkaListener` and `groupId`, hard-coded topic names, soft-delete filters (`@SQLRestriction`,
  `@Where`), native queries, HTTP clients without timeouts, broad catches.
- **Dependencies**: Maven `artifactId` and Gradle dependency lines added or removed.
- **Front-end** (React/Next.js, Vue/Nuxt, Angular, Svelte; `range.stack` says which stacks the
  repository has): pages and routes, API client files, front-end config (next/vite/tsconfig, middleware),
  i18n files; environment variables newly read (`process.env.X`, `import.meta.env.X`); API paths called
  added or removed; `package.json` dependencies with old and new version (major bumps flagged); rules on
  added lines: raw HTML (`dangerouslySetInnerHTML`, `v-html`, `innerHTML`, `{@html}`), `eval`, tokens in
  web storage, public env vars named like secrets, hard-coded URLs, `target=_blank` without `rel`,
  auth guards added or removed, `@ts-ignore`/`any`, `console.log`. Front-end test files are tests.
- **Review files**: changed source files (Java and front-end, no tests or generated files), largest change
  first: the `code-reviewer` agent's reading list.

The scan is a pointer, not a verdict: each flag is checked against the code before it is reported.

## Deep code review (`code-reviewer` agent)

Opus at medium effort: it reads each changed file's diff with 25 lines of context, opens the file and its
callers when a hunk depends on them, and judges the code against the PR's intent. Checklists for both
stacks (transactions, concurrency, idempotency, N+1, status codes; XSS, guards, effects, races, API
contract). It reports at most about 15 defects with file:line, consequence and fix, lists the files it did
not read (budget about 40 files or 8000 changed lines), and never edits or builds.

## Merge check (`compare`)

In a service repository:

```text
so sánh staging với release
staging còn commit nào chưa lên release, merge lên thì ảnh hưởng gì
so sánh nhánh hotfix/x với master
```

The skill fetches both branches, runs `branch_compare.py`, checks the keys the merge needs against
each environment (`env_overrides.py`), reads what was flagged and writes
`review-reports/<report name>.md` from the template.

## How commits are sorted

`git log --cherry-mark` over `target...source`, merges skipped, then:

| Group | Meaning |
|---|---|
| missing | the merge brings it and changes at least one of its files |
| duplicates | a later copy of a missing commit with the same patch id (a branch merged twice); counted once |
| already on target | same patch on the target (paired by patch id), or a target commit says `cherry picked from commit <sha>` |
| maybe on target | a target commit has the same subject but a different patch: to confirm |
| reverted | the commit and its revert are both in the batch |
| no net effect | the merge changes none of its files (squashed or re-done on the target) |
| target only | only on the target; `back_merge_effect` tells whether merging back would change anything |

Plus tickets (`ABC-123` in the subject) with commits on both sides, migrations added only on the
target, and for each conflicted file the commits of each side that touch it.

## How the impact is judged

On the diff from the target to the trial merge result (`git merge-tree --write-tree`), so cherry-picked
commits add nothing and hotfixes on the target stay. Conflicted files are judged on the source side.
The diff goes through `pr_scan.py`'s rules (SQL per database, Liquibase, Flyway, config, Kafka,
Java), plus:

- config keys read without a default (`@Value("${x}")`, `${X}` in packaged config) that no packaged
  config file sets: they must exist in the target environment's config;
- config files of a target environment (`application-uat.yml`, `values-prod.yaml`, `deploy/prod/`)
  and of staging; staging/dev/localhost values in shared config (value never printed);
- topics declared in code (`NewTopic`, `TopicBuilder`), message classes with fields removed or changed,
  `.avsc`/`.proto` changes;
- `-SNAPSHOT` dependencies and version properties (`x.version: old → new`, Spring Boot/Java flagged);
- areas worth a sign-off per commit: money/price, permissions, scheduled jobs, outgoing messages,
  deletes.

## Changes between two revisions (`changes`)

```text
liệt kê thay đổi giữa v1.4.0 và v1.5.0
release này thay đổi gì                          (from = the tag before)
thay đổi giữa release và staging của các service trong ~/code/services
```

The skill checks the vault, finds the services (`ref_changes.py --discover <dir>`), fetches, then starts
one `service-changes` agent per service in parallel (Sonnet: reading code and writing the report is the
expensive part), all with the same run stamp. Each runs `ref_changes.py`, reads what was flagged and writes the report from
`changes-template.md`. The skill links the services (`ref_changes.py --cross`), writes the run summary,
and a `vault-publisher` agent (Haiku: two commands) writes everything into the vault.

What `ref_changes.py` compares, at both revisions:

| Area | How |
|---|---|
| Commits | `git log --cherry-mark from...to`: new, already in `from`, revert pairs, duplicates, no net effect, only in `from` |
| Endpoints | every `@RestController`/`@FeignClient` at both revisions, by method and path: headers, params, path variables, body, return type, security; breaking = new required header/param, other body/return/path, security |
| Request/response | changed data classes, fields with type, validation, JSON name; breaking only when the class reaches the outside (used by an endpoint or Feign call, a field of one, or a web/external/message package) |
| Business code | enum constants, `@Scheduled`, `@KafkaListener`, changed logic files by size, sensitive areas per commit |
| Config | every key of `application*.yml/properties` with its value before and after (YAML nesting, lists, profiles); secrets and URL passwords masked |
| Database | operations of new and changed migrations (SQL, Liquibase XML/YAML/formatted SQL; rollback blocks left out); entity tables and columns; entity columns without a migration, migrations dropping a mapped column, removed or reordered persisted enum constants |
| Dependencies | Maven (properties resolved) and Gradle coordinates and versions |
| Across services | a changed endpoint another service of the run calls through Feign; a topic changed in one service and configured in another |

Notes go to `<vault>/<dir>/<repo>/<yyyymmdd-HHMM>-<from>~<to>.md` (the repository folder is created when
missing) with properties (`repo`, `stamp`, `from`, `to`, `commits`, `breaking`, `migrations`,
`findings_high`, `tags`) and the marker `generated_by: branch-compare`; a run over several services adds
`<vault>/<dir>/_tong-hop/<stamp>-<from>~<to>.md` with each service's business flows to accept (one short
table per service, taken from section 1 of its note), the service table and a Dataview query; `--include`
adds notes of another stamp. In section 1 the before/after cell is split with `<br>` (a Markdown table cell
cannot hold a line break; Obsidian renders it). The report itself starts
with the business flows to accept (for BA/Test, no class names), then a copy-ready config block, commits and
API, then Kafka, database (schema, migrations, data and seeds), dependencies, short. A rerun overwrites the skill's own notes; a note
without the marker is never overwritten.

Then `obsidian_note.py sync` asks the running Obsidian app, through its CLI (`obsidian command
id=git-vault-sync:sync vault=<name>`), to run the vault's sync plugin, and waits until the run's notes are
committed (and tells whether the commit was pushed). The plugin does the git work with its own settings and
token; the script never runs git commands that change the vault. Obsidian not running: exit 5, the notes stay
in the vault and the plugin's sync on startup picks them up. Any sync plugin works if its command id is set.

```dataview
TABLE from, to, commits, breaking, migrations FROM "code-changes" WHERE type = "code-changes" SORT date DESC
```

## Settings

In `~/.dev-cli/.env` (a process environment variable with the same name wins). The names keep the former
plugins' prefixes so that existing settings keep working.

| Key | Used by | Meaning |
|---|---|---|
| `BITBUCKET_URL` | review-pr | Bitbucket Server/DC base URL (`https://` only). Optional when the user gives a full PR link |
| `BITBUCKET_TOKEN` | review-pr | HTTP access token with repository read permission, sent as Bearer. Or `BITBUCKET_USER` + `BITBUCKET_PASSWORD` |
| `BITBUCKET_CA_BUNDLE` | review-pr | CA file when Bitbucket uses an internal certificate authority. TLS verification is never turned off |
| `SPRING_PR_REVIEW_DIALECT` | all | database for the SQL rules when detection is wrong: `mariadb`, `mysql`, `postgresql`, `oracle`, `sqlserver`. Default `auto` |
| `SPRING_PR_REVIEW_APP_CONFIG` | review-pr, compare | path of app-config-review's `app_config.py` when it is not found automatically |
| `OBSIDIAN_VAULT` | changes | vault folder (the one holding `.obsidian/`). Required for `changes` |
| `BRANCH_COMPARE_OBSIDIAN_DIR` | changes | folder inside the vault. Default `code-changes` |
| `BRANCH_COMPARE_OBSIDIAN_SYNC` | changes | `on`/`off`: run the vault's sync command after writing. Default `on` |
| `BRANCH_COMPARE_OBSIDIAN_SYNC_COMMAND` | changes | Obsidian command id that syncs the vault. Default `git-vault-sync:sync` |
| `OBSIDIAN_CLI` | changes | Obsidian's command line (Settings > General > Command line interface). Default `obsidian` |

The environment table needs the `app-config-review` plugin and its `APP_CONFIG_*` settings. Telegram sending
uses `telegram-send`. Notes in the vault keep the marker `generated_by: branch-compare`, so notes written
before the rename are still recognised.

## Moving from spring-pr-review / branch-compare

Run `dev-cli --agent-resource init` (installs `code-review`), then remove the former plugins, which the
status lists as orphans: `dev-cli --agent-resource clean --only plugin/spring-pr-review` (and
`plugin/branch-compare` if it was installed). Do not use `uninstall`: it removes everything dev-cli installed. Skill
names change from `spring-pr-review:review-pr` and `branch-compare:compare|changes` to
`code-review:review-pr|compare|changes`.

## Limits

- Bitbucket Server / Data Center REST API 1.0 only (not Bitbucket Cloud). Read-only: no approval,
  comment or merge.
- Config keys are matched by name; `env_overrides.py` matches YAML leaf names against every full key that
  ends with them, so a leaf like `group-id` can match several keys.
- A `DELETE` or `UPDATE` flagged without `WHERE` may have the clause on the next line; read the statement.
- The trial merge stores unreferenced objects in `.git/objects` (removed by `git gc`); `--no-merge-check`
  skips it.
- `java_inventory.py` reads declarations, not types: a path built from a constant of another file stays
  `{Constant.NAME}`, inherited mappings and fields of a superclass are not followed, and Lombok is not
  expanded (fields are read as declared).
- Data classes are compared where their file changed; an exposed class reached only through an unchanged
  class is reported without its endpoint.
- Migration operations come from the statements' text: dynamic SQL (`PREPARE`/`EXECUTE`) and Java
  migrations are listed, not read.
- The trial merge needs git 2.38 or later and stores unreferenced objects in `.git/objects` (removed by
  `git gc`); without it (`--no-merge-check`) the impact is judged on `merge-base..source`, cherry-picks
  included.
- "No net effect" and `back_merge_effect` work per file: a commit counts as having an effect when the
  merge changes any file it touched.
- Config keys are matched by name with relaxed binding; YAML nesting in a diff is not tracked.
- Front-end rules work on the added lines' text: a raw-HTML or token line built across several lines, or
  through a helper, is missed; the `code-reviewer` agent is the second pass.
- The `code-reviewer` agent reads a budget of files; large ranges list what it did not read.
