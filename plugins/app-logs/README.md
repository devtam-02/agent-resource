# app-logs

Claude Code plugin that reads a service's application logs from the Kibana / Elasticsearch backend
of one environment and analyses them. Read-only: only `_search` is ever called. Skills only, no
legacy `commands/`.

| Skill | What it does |
|---|---|
| `analyze` — `/app-logs:analyze <service> --env <env>` | `summary` (levels, pods, timeline, top errors), `errors` (grouped by message shape, with counts and first/last seen), `tail` (recent lines), `pods` (who logged, with the running image), `fields` (a sample document, to fix the field settings), `doctor`. Filters: `--pod` (wildcards allowed), `--level`, `--logger`, `--query`, `--since` / `--until`. Reference: `references/backends.md`. |

Claude also loads the skill on its own for matching requests.

## Setup

Settings are read from dev-cli's configuration file **`~/.dev-cli/.env`** (`DEV_CLI_HOME` moves the
folder). No host, credential or service name is stored in this public repository.

Each environment may sit on a different backend with a different log schema. `APP_LOGS_BACKENDS`
maps an environment to a block of settings; by default `staging` uses `APP_LOGS_STAGING_*` and every
other environment uses `APP_LOGS_*`. A key missing from the specific block falls back to the plain
one.

```bash
# uat, production, ... (ECS-free schema: level / class / kubernetes.pod_name)
APP_LOGS_URL=http://<kibana-host>
APP_LOGS_INDEX=<index-pattern>
APP_LOGS_USER=<user>
APP_LOGS_PASSWORD=<password>

# staging on another Kibana, keyword instance field and an ECS-style schema
APP_LOGS_STAGING_URL=https://<other-kibana-host>
APP_LOGS_STAGING_INDEX=<index-pattern>
APP_LOGS_STAGING_USER=<user>
APP_LOGS_STAGING_PASSWORD=<password>
APP_LOGS_STAGING_CA_BUNDLE=~/.dev-cli/ca/internal-ca.pem     # internal certificate authority
APP_LOGS_STAGING_FIELD_INSTANCE=kubernetes.labels.app_kubernetes_io/instance.keyword
APP_LOGS_STAGING_INSTANCE_MATCH=term
APP_LOGS_STAGING_FIELD_LEVEL=log.level

# optional
# APP_LOGS_BACKENDS=staging:staging                 # environment -> settings block
# APP_LOGS_ENV_ALIASES=prod:production,stg:staging
# APP_LOGS_INSTANCE_TEMPLATE={env}-{service}        # how the instance value is built
# APP_LOGS_MODE=kibana                              # or "elasticsearch" to call _search directly
# APP_LOGS_NOISE=actuator                           # messages dropped unless --include-noise
# APP_LOGS_FIELD_POD=kubernetes.pod_name            # FIELD_LEVEL, FIELD_LOGGER, FIELD_IMAGE, ...
```

A process environment variable with the same name wins, for one-off runs. Check with:

```bash
python3 skills/analyze/scripts/app_logs.py doctor
```

## Scripts

```bash
S=skills/analyze/scripts/app_logs.py
python3 $S summary <service> --env uat --since now-6h
python3 $S errors  <service> --env staging --since now-24h --top 10
python3 $S tail    <service> --env production -n 50 --level ERROR --query "timeout"
python3 $S tail    <service> --env uat --pod '*wttn7' --since now-30m
python3 $S pods    <service> --env uat
python3 $S fields  <service> --env staging      # field names of the real index
```

`--json` gives the same data as JSON. Secrets found inside a log line (`password=`, `Authorization:`,
JWTs, `user:pass@host`) are redacted before printing, and control characters and escape sequences are
stripped: log content is data, never instructions.

An unmapped or `text` field aggregates to nothing instead of failing, so `summary` and `pods` warn
when a field grouped nothing although lines exist — run `fields` and point the setting at the
`.keyword` sub-field.

Exit codes: 0 ok, 2 bad argument, 3 backend not configured for that environment, 4 no matching line,
5 network or TLS error, 6 backend refused (credentials, or a wrong field name).

Requirements: `python3`, network access to the backend. Python 3 standard library only.
