# spring-pr-review

Review a Spring Boot pull request, with the answers in a fixed order: what each commit does, how far
the change reaches, which config keys and database migrations it changes (and what each environment
overrides); then code findings. Reads Bitbucket Server/DC when configured; scans the git range locally.

```text
skills/review-pr/
  SKILL.md                       the workflow and rules
  scripts/bitbucket_pr.py        Bitbucket PR, GET only: title, branches, commits, merge status, CI builds
  scripts/pr_scan.py             git range scan: commits, categories, risk findings, trial merge (JSON or text)
  scripts/env_overrides.py       config keys the PR changes x staging/uat/production overrides (via app-config-review)
  scripts/plugin_settings.py     settings reader (~/.dev-cli/.env)
  references/report-template.md  the report layout (Vietnamese)
  references/checklist.md        Spring Boot checks: migration (Liquibase, Flyway, per DB), Kafka, config, JPA, API, ...
tests/                           unittest suite, offline (python3 -m unittest discover -s tests -t tests)
```

## Use

In a Spring Boot repository:

```text
review PR https://<bitbucket>/projects/PROJ/repos/svc/pull-requests/13
review nhánh feature/order vào staging
```

The skill reads the PR (if Bitbucket is configured), fetches both branches, runs `pr_scan.py`, compares
the changed config keys with each environment (`env_overrides.py`), reads the flagged migrations,
config and commits, and writes `review-reports/<report name>.md` from the template. It sends to
Telegram only when asked.

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

The scan is a pointer, not a verdict: each flag is checked against the code before it is reported.

## Settings

In `~/.dev-cli/.env` (or the process environment):

| Key | Purpose |
|---|---|
| `BITBUCKET_URL` | Bitbucket Server/DC base URL (`https://` only). Optional when the user gives a full PR link. |
| `BITBUCKET_TOKEN` | HTTP access token with repository read permission, sent as Bearer. Or `BITBUCKET_USER` + `BITBUCKET_PASSWORD`. |
| `BITBUCKET_CA_BUNDLE` | CA file when Bitbucket uses an internal certificate authority. TLS verification is never turned off. |
| `SPRING_PR_REVIEW_DIALECT` | Database for the SQL rules when detection is wrong: `mariadb`, `mysql`, `postgresql`, `oracle`, `sqlserver`. Default `auto`. |
| `SPRING_PR_REVIEW_APP_CONFIG` | Path of app-config-review's `app_config.py` when it is not found automatically. |

Environment comparison needs the `app-config-review` plugin and its `APP_CONFIG_*` settings. Telegram
sending uses `telegram-send`.

## Shared with branch-compare

`pr_scan.py`, `env_overrides.py` and `plugin_settings.py` are copied into
`plugins/branch-compare/skills/compare/scripts/`. After editing one here, copy it there and run both
test suites (branch-compare's `test_vendored.py` fails while the copies differ).

## Limits

- Bitbucket Server / Data Center REST API 1.0 only (not Bitbucket Cloud). Read-only: no approval,
  comment or merge.
- Config keys are matched by name; `env_overrides.py` matches YAML leaf names against every full key that
  ends with them, so a leaf like `group-id` can match several keys.
- A `DELETE` or `UPDATE` flagged without `WHERE` may have the clause on the next line; read the statement.
- The trial merge stores unreferenced objects in `.git/objects` (removed by `git gc`); `--no-merge-check`
  skips it.
