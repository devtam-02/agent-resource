# SQL Runner: what the tool rejects and the rewrite that works

The web SQL Runner checks each statement with its own lightweight SQL parser (to find the target
tables for its permission check) before MariaDB sees it. Most odd failures come from that parser,
not from the database. `sql_runner.py lint` catches the patterns marked *lint*.

| Symptom | Cause | Rewrite |
|---|---|---|
| `403 DDL operation 'DROP' is not allowed via SQL Runner` (*lint*) | `DROP TABLE`, `DROP VIEW` are blocked server-side | none through the tool: needs direct database access |
| `500 Finding tables from CreateSequence is not supported` (*lint*) | the parser does not know `CREATE SEQUENCE` | none: direct access, or let the service's framework (e.g. Spring Batch) create it on first start |
| `403 ... target tables could not be resolved for a write/DDL statement` on `CREATE VIEW` (*lint*) | `DEFINER=`, `SQL SECURITY`, `ALGORITHM=` before `AS SELECT` | `CREATE OR REPLACE VIEW name AS SELECT ...`; the view then runs with the connecting user's privileges: check the schema does not rely on a pinned definer |
| same 403 on `CREATE TABLE ... PARTITION BY ...` (*lint*) | partition clause inside `CREATE TABLE` | plain `CREATE TABLE`, then `ALTER TABLE ... PARTITION BY ... (...)` |
| same 403 on an `INSERT` with `'...can\'t...'` (*lint*) | backslash-escaped quote | doubled quote: `'...can''t...'` |
| same 403 on `ALTER TABLE ... DROP COLUMN type` (*lint warns*) | a keyword-like column name | backtick it: `` DROP COLUMN `type` `` |
| `400 Operation affects different number of rows than expected (N/0). Rollback execute aborted.` | writes need `affectedRows` in the request (the browser's confirmation fills it in) | `--affected-rows N` with the exact count; nothing is committed on a mismatch |
| `PARTITION BY RANGE COLUMNS` fails on a `TIMESTAMP` column (*lint warns*) | MariaDB limitation (only DATE/DATETIME/integers/strings) | make that column `DATETIME(3)`, after checking the code does not depend on TIMESTAMP's UTC conversion |
| error text cut off | the tool truncates query echo + reason to one short budget | reproduce with the shortest statement (short names, one line) so the reason fits |

The browser's "this query may change data" dialog is client-side only: the API runs a statement as
soon as it is sent. That is why the script is read-only by default and asks for `--write` and a confirmation code the user types back (`--confirm`).

## Useful read queries

```sql
select schema_name from information_schema.schemata order by schema_name;

-- completeness check across many schemas at once (BASE TABLE / VIEW / SEQUENCE)
select table_schema, table_type, count(*) from information_schema.tables
where table_schema in ('schema_a', 'schema_b') group by table_schema, table_type
order by table_schema, table_type;

select table_name, column_name, column_type, is_nullable, column_default
from information_schema.columns where table_schema = 'schema_a' and table_name = 't';
```

## Rolling DDL out from a design document

1. `tables <schema>` first: a framework (Spring Batch tables and sequences) may already have
   created some objects. Skip what exists.
2. One statement per `.sql` file, in the document's order (usually foreign-key safe); `lint` each.
3. Split `PARTITION BY` out of `CREATE TABLE`; a missing sequence is a blocker (see above).
4. Verify with the completeness query and a few `describe` against the document.
5. Compare with the same schema on another environment (staging) for objects the document does not
   list (views especially): the document is rarely a complete inventory.

Project-specific notes (connection names, schema lists, rollout state) do not belong in this public
repository: keep them in a private note, e.g. the Obsidian vault.
