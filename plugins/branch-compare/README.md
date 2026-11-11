# branch-compare

Compare two branches before merging one into the other, by default `staging` into `release`: which
commits the target is missing and what each does, then what the merge would really change and what
has to be done before and after it. Read-only.

```text
skills/compare/
  SKILL.md                       the workflow and rules
  scripts/branch_compare.py      commit comparison, trial merge, impact scan (JSON or text)
  scripts/pr_scan.py             scan rules, copied from spring-pr-review (keep identical)
  scripts/env_overrides.py       config keys x staging/uat/production (via app-config-review), copied
  scripts/plugin_settings.py     settings reader (~/.dev-cli/.env), copied
  references/report-template.md  the report layout (Vietnamese)
  references/checklist.md        merge risks: Kafka, migration, config, dependency, CI/CD, API, conflicts, sensitive code
tests/                           unittest suite, offline (python3 -m unittest discover -s tests -t tests)
```

## Use

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
The diff goes through spring-pr-review's rules (SQL per database, Liquibase, Flyway, config, Kafka,
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

## Settings

None of its own. It reads spring-pr-review's `SPRING_PR_REVIEW_DIALECT` (database for the SQL rules)
and `SPRING_PR_REVIEW_APP_CONFIG` (path of app-config-review's `app_config.py`), and needs the
`app-config-review` plugin with its `APP_CONFIG_*` settings for the environment table.

## Editing the shared scripts

`pr_scan.py`, `env_overrides.py` and `plugin_settings.py` are spring-pr-review's. Edit them there,
copy them here, run both test suites; `tests/test_vendored.py` fails while the copies differ.

## Limits

- The trial merge needs git 2.38 or later and stores unreferenced objects in `.git/objects` (removed by
  `git gc`); without it (`--no-merge-check`) the impact is judged on `merge-base..source`, cherry-picks
  included.
- "No net effect" and `back_merge_effect` work per file: a commit counts as having an effect when the
  merge changes any file it touched.
- Config keys are matched by name with relaxed binding; YAML nesting in a diff is not tracked.
