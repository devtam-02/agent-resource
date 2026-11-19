#!/usr/bin/env python3
"""PreToolUse hook (Bash): a command that may write to a database never runs without the user.

- sql_runner.py ... --write --confirm CODE  -> ask: Claude Code shows the statement, the user approves or not
  (the dry run without --confirm sends nothing and is let through).
- a direct call to the SQL Runner API (curl/wget/python on .../sql-runner/execute) -> deny: use sql_runner.py.
- a database client (mysql, mariadb, psql, sqlplus, sqlcmd, mongosh, mongo, clickhouse-client, cqlsh, redis-cli)
  with a write statement or fed from a file, and migration tools (flyway migrate/clean/repair, liquibase
  update/rollback/drop-all, mongock) -> ask.

The decision is printed as hook JSON on stdout; nothing printed means "no opinion". There is no switch to turn
this off: the user asked for writes to always go through them (rules/db-write-safety.md).
"""

import json
import re
import shlex
import sys

CLIENTS = r"(mysql|mariadb|psql|sqlplus|sqlcmd|mongosh|mongo|clickhouse-client|cqlsh|redis-cli)"
# the client must be the command being run: start of a command, after sudo/env/time, inside docker/kubectl exec
DB_CLIENT_RE = re.compile(
    r"(?:^|[;&|(\n]|\$\()\s*(?:(?:sudo|env|time|nice|exec)\s+(?:-\S+\s+|\w+=\S*\s+)*)*"
    r"(?:(?:docker|podman)\s+(?:compose\s+)?exec\s+(?:-\S+\s+(?:\S+\s+)?)*?\S+\s+|"
    r"kubectl\s+exec\b[^|;&]*?\s--\s+)?(?:\S*/)?" + CLIENTS + r"(?=\s|$|;|\|)")
WRITE_SQL_RE = re.compile(
    r"\b(insert|update|delete|replace|merge|upsert|create|alter|drop|truncate|rename|grant|revoke|call|load\s+data)\b", re.I)
WRITE_MONGO_RE = re.compile(
    r"\.(insert(One|Many)?|update(One|Many)?|delete(One|Many)?|replaceOne|findOneAnd(Update|Replace|Delete)|remove|"
    r"bulkWrite|drop|dropDatabase|createIndex|dropIndex|renameCollection)\s*\(")
WRITE_REDIS_RE = re.compile(r"\b(set|del|hset|hdel|lpush|rpush|sadd|srem|zadd|zrem|expire|flushdb|flushall|rename|incr|decr)\b", re.I)
FED_FROM_FILE_RE = re.compile(r"(?<![<>])<\s*[^<\s]|\bsource\s+\S+\.sql\b|\s-f\s+\S+\.sql\b|--file[= ]\S+", re.I)
MIGRATION_RE = re.compile(
    r"\bflyway\b[^|;&]*\b(migrate|clean|repair|baseline|undo)\b|flyway:(migrate|clean|repair|baseline|undo)\b|"
    r"\bflyway(Migrate|Clean|Repair|Baseline)\b|\bliquibase\b[^|;&]*\b(update|rollback|drop-all|dropAll|update-sql)\b|"
    r"liquibase:(update|rollback|dropAll)\b|\bmongock\b", re.I)
API_RE = re.compile(r"sql-runner/execute|connection-info/get-all-public|/vms/api/auth/login", re.I)


def decision(kind, reason):
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": kind,
                                   "permissionDecisionReason": reason}}


def option(words, name):
    for i, w in enumerate(words):
        if w == name and i + 1 < len(words):
            return words[i + 1]
        if w.startswith(name + "="):
            return w.split("=", 1)[1]
    return None


def check_sql_runner(command):
    if "sql_runner" not in command:
        return None
    if re.search(r"\bimport\s+sql_runner\b|from\s+sql_runner\s+import|\.execute\s*\(", command):
        return decision("deny", "Không gọi hàm của sql_runner trực tiếp: chạy script `sql_runner.py query ...`.")
    if "--write" not in command:
        return None
    try:
        words = shlex.split(command)
    except ValueError:
        words = command.split()
    if "--confirm" not in command:
        return None  # dry run: prints the plan and the code, sends nothing
    conn = option(words, "--conn") or "(SQL_RUNNER_CONNECTION)"
    rows = option(words, "--affected-rows")
    sql_file = option(words, "--file")
    after = words[words.index("query") + 1:] if "query" in words else []
    sql = next((w for w in after if not w.startswith("-") and w not in (conn, rows, sql_file, option(words, "--confirm"))), "")
    what = f"file {sql_file}" if sql_file else (sql[:400] + ("…" if len(sql) > 400 else ""))
    reason = (f"GHI DATABASE qua SQL Runner — connection `{conn}`"
              + (f", số dòng {rows}" if rows else "") + (" , PRODUCTION (--allow-prod)" if "--allow-prod" in words else "")
              + f": {what}. Chỉ đồng ý nếu bạn đã đọc kế hoạch và tự gõ mã xác nhận.")
    return decision("ask", reason)


def check(command):
    found = check_sql_runner(command)
    if found:
        return found
    if API_RE.search(command) and re.search(r"\b(curl|wget|http|httpie|python3?|node|requests)\b", command):
        return decision("deny", "Không gọi thẳng API của SQL Runner: dùng `sql_runner.py` (chỉ đọc mặc định, "
                                "ghi phải xác nhận nhiều bước).")
    m = MIGRATION_RE.search(command)
    if m:
        return decision("ask", f"Lệnh migration database (`{m.group(0).strip()}`): chạy sẽ thay đổi schema/dữ liệu. "
                               "Chỉ đồng ý khi bạn đã yêu cầu đúng việc này, đúng môi trường.")
    client = DB_CLIENT_RE.search(command)
    if not client:
        return None
    name = client.group(1)
    body = command[client.end():]
    if name == "redis-cli":
        hit = WRITE_REDIS_RE.search(body)
    elif name in ("mongosh", "mongo"):
        hit = WRITE_MONGO_RE.search(command)
    else:
        hit = WRITE_SQL_RE.search(command)  # the statement may come before the client: echo "..." | mysql
    if hit:
        return decision("ask", f"`{name}` với câu lệnh ghi (`{hit.group(0).strip()}`): sẽ thay đổi database. "
                               "Chỉ đồng ý khi bạn đã đọc đúng câu lệnh, đúng database và tự yêu cầu chạy.")
    if FED_FROM_FILE_RE.search(body) or command.lstrip().startswith(("cat ", "<")) and "|" in command:
        return decision("ask", f"`{name}` nhận câu lệnh từ file hoặc pipe, không xem được nội dung: có thể ghi database. "
                               "Chỉ đồng ý khi bạn đã đọc file đó.")
    return None


def main():
    try:
        event = json.load(sys.stdin)
    except ValueError:
        return 0
    if event.get("tool_name") != "Bash":
        return 0
    command = (event.get("tool_input") or {}).get("command") or ""
    result = check(command)
    if result:
        print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
