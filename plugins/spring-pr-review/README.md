# spring-pr-review

Review a Spring Boot pull request from the local git range, with the answers in a fixed order:
what each commit does, how far the change reaches, and which config and database migrations it
changes; then code findings. Read-only on the repository.

```text
skills/review-pr/
  SKILL.md                      the workflow and rules
  scripts/pr_scan.py            read-only git scan: commits, categories, risk findings (JSON or text)
  references/report-template.md the report layout (Vietnamese)
  references/checklist.md       Spring Boot checks for migration, config, JPA, API, transactions, security
```

## Use

In a Spring Boot repository, after `git fetch`:

```text
review PR feature/order vào staging
```

The skill runs:

```bash
python3 scripts/pr_scan.py --base origin/staging --head origin/feature/order --format text
```

then reads the flagged migrations, config files and commits, and writes
`review-reports/review-<head>-<base>-<date>.md`. It sends to Telegram only when asked.

## What the scan reports

- **Commits**: sha, author, date, subject, body (secret-looking values redacted), files, categories, flags.
- **Migrations**: Flyway/Liquibase files, status (added, modified, deleted), SQL statement kinds.
  A migration that already existed on the target branch and is now modified or deleted is flagged high.
- **SQL rules** (added lines only): DROP, TRUNCATE, RENAME, type change, NOT NULL without DEFAULT,
  DELETE or UPDATE without WHERE, CREATE INDEX without CONCURRENTLY.
- **Config keys** (by name, not by full path): added, changed, removed in `application*.yml`,
  `bootstrap*`, `config/`, helm and `.env` files. Flags: `ddl-auto` create/update, actuator
  `include=*`, secret-looking keys with a literal value (value never printed), `open-in-view`.
- **Java rules**: JPA mapping, `@Transactional`, endpoint mapping, `FetchType.EAGER`, `permitAll()`
  and CSRF off, async entry points, native queries, HTTP clients without timeouts, broad catches.
- **Dependencies**: Maven `artifactId` and Gradle dependency lines added or removed.

The scan is a pointer, not a verdict: each flag is checked against the code before it is reported
as a problem.

## Limits

- Config keys are matched by name only; YAML nesting is not tracked on a diff. The skill reads the file
  when the context matters.
- Reads the git range as it exists locally. Run `git fetch` first.
- Bitbucket pull-request comments and approvals are not touched.
- A `DELETE` or `UPDATE` flagged without `WHERE` may have the clause on the next line; read the statement.

## Settings

None. The plugin reads only the repository it runs in. Telegram sending uses `telegram-send`, whose
settings live in `~/.dev-cli/.env`.
