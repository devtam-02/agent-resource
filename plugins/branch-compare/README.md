# branch-compare

Two skills on the git history of Spring services, both read-only on the repositories:

- **`compare`**: before merging one branch into another, by default `staging` into `release`: which
  commits the target is missing and what each does, then what the merge would really change and what
  has to be done before and after it.
- **`changes`**: what changed between two revisions (tags, branches or commits) of one or more
  services, for developers, written into an Obsidian vault.

```text
skills/compare/
  SKILL.md                       the merge workflow and rules
  scripts/branch_compare.py      commit comparison, trial merge, impact scan (JSON or text)
  scripts/ref_changes.py         changes between two revisions; --discover, --cross (JSON or text)
  scripts/java_inventory.py      Java reader: endpoints, Feign calls, DTO fields, entities, enums, jobs, listeners
  scripts/obsidian_note.py       writes reports and the run summary into the vault
  scripts/pr_scan.py             scan rules, copied from spring-pr-review (keep identical)
  scripts/env_overrides.py       config keys x staging/uat/production (via app-config-review), copied
  scripts/plugin_settings.py     settings reader (~/.dev-cli/.env), copied
  references/report-template.md  the merge report layout (Vietnamese)
  references/checklist.md        merge risks: Kafka, migration, config, dependency, CI/CD, API, conflicts, sensitive code
skills/changes/
  SKILL.md                       the changes workflow: scope, fetch, one subagent per service, summary, vault
  references/changes-template.md the per-service report layout (Vietnamese, Obsidian callouts)
agents/
  service-changes.md             one service's report (model: sonnet, effort: high; opus for very large ranges)
  vault-publisher.md             writes the reports into the vault (model: haiku)
tests/                           unittest suite, offline (python3 -m unittest discover -s tests -t tests)
```

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

In `~/.dev-cli/.env` (a process environment variable with the same name wins):

| Key | Used by | Meaning |
|---|---|---|
| `OBSIDIAN_VAULT` | `changes` | vault folder (the one holding `.obsidian/`). Required for `changes` |
| `BRANCH_COMPARE_OBSIDIAN_DIR` | `changes` | folder inside the vault. Default `code-changes` |
| `BRANCH_COMPARE_OBSIDIAN_SYNC` | `changes` | `on`/`off`: run the vault's sync command after writing. Default `on` |
| `BRANCH_COMPARE_OBSIDIAN_SYNC_COMMAND` | `changes` | Obsidian command id that syncs the vault. Default `git-vault-sync:sync` (Git Vault Sync) |
| `OBSIDIAN_CLI` | `changes` | Obsidian's command line (Settings > General > Command line interface). Default `obsidian` |
| `SPRING_PR_REVIEW_DIALECT` | both | database for the SQL rules (spring-pr-review's setting) |
| `SPRING_PR_REVIEW_APP_CONFIG` | `compare` | path of app-config-review's `app_config.py` |

`compare` needs the `app-config-review` plugin with its `APP_CONFIG_*` settings for the environment table.

## Editing the shared scripts

`pr_scan.py`, `env_overrides.py` and `plugin_settings.py` are spring-pr-review's. Edit them there,
copy them here, run both test suites; `tests/test_vendored.py` fails while the copies differ.

## Limits

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
