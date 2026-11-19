#!/usr/bin/env python3
"""Run SQL through a web "SQL Runner" admin tool's REST API (login, list connections, execute).

The tool fronts database connections (MariaDB/MySQL) that are reachable only through it. This script
calls its API directly instead of driving the browser UI. Read-only by default: a statement that is not
SELECT / SHOW / DESCRIBE / EXPLAIN is refused unless --write is given and confirmed in two steps (a dry
run prints a confirmation code; only a run with --confirm <that code> sends it), a write on a
production-looking connection also needs --allow-prod, and statements the tool is known to reject are
stopped before they are sent, with the rewrite that works (references/gotchas.md).

Settings (~/.dev-cli/.env, or the process environment; VMS_SQL_RUNNER_HOST / _USERNAME / _PASSWORD
are accepted as the older names):
  SQL_RUNNER_URL          base URL of the tool, e.g. https://sql-runner.example.com. Required.
  SQL_RUNNER_USER         account name. Required.
  SQL_RUNNER_PASSWORD     password. Required. Never printed.
  SQL_RUNNER_API_PATH     API prefix. Default: /vms/api
  SQL_RUNNER_CA_BUNDLE    CA file for an internal certificate authority (TLS is never turned off).
  SQL_RUNNER_TIMEOUT      seconds per request. Default: 60
  SQL_RUNNER_CONNECTION   default connection name when --conn is left out.
  SQL_RUNNER_WRITE_CONNECTIONS  connections (globs, comma separated, case-insensitive) where writes are
                          allowed, e.g. uat_*. Not set: every write is refused.
  SQL_RUNNER_PROD_PATTERN connection names treated as production even inside the allow list (globs).
                          Default: a name with the word prod, prd, production, live, dr or standby

  sql_runner.py doctor
  sql_runner.py connections
  sql_runner.py schemas  [--conn C]
  sql_runner.py tables   SCHEMA [--conn C]
  sql_runner.py describe SCHEMA.TABLE [--conn C]
  sql_runner.py query "SQL" ["SQL" ...] [--conn C] [--max-rows 200] [--format table|json|csv] [--out F]
  sql_runner.py query --file stmt.sql [--conn C]
  sql_runner.py query "INSERT ..." --conn C --write --affected-rows 1            # dry run: plan + code
  sql_runner.py query "INSERT ..." --conn C --write --affected-rows 1 --confirm CODE [--allow-prod]
  sql_runner.py lint  "SQL" | --file stmt.sql

Exit codes: 0 ok, 2 bad usage or refused before sending (read-only, not confirmed, production,
known-unsupported statement, several statements in one), 3 not configured, 4 connection not found,
5 network or TLS error, 6 the tool refused or failed (login, permission, SQL error).
"""

import argparse
import csv
import fnmatch
import hashlib
import io
import json
import os
import re
import ssl
import sys
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from plugin_settings import dev_cli_env_path, setting  # noqa: E402

READ_KEYWORDS = {"select", "show", "describe", "desc", "explain", "with", "help"}
READ_BUT_WRITE_RE = re.compile(r"\binto\s+(outfile|dumpfile)\b|\bfor\s+update\b|\block\s+in\s+share\s+mode\b", re.I)
DDL_KEYWORDS = {"create", "alter", "drop", "truncate", "rename"}
DML_KEYWORDS = {"insert", "update", "delete", "replace"}
IDENT_RE = re.compile(r"^[A-Za-z0-9_$]+$")


class RunnerError(Exception):
    def __init__(self, message, code=2):
        super().__init__(message)
        self.code = code


def out(text=""):
    print(text)


# ------------------------------------------------------------------ settings

def conf(name, old=None, default=""):
    value, _ = setting(name)
    if not value and old:
        value, _ = setting(old)
    return value or default


def config():
    url = conf("SQL_RUNNER_URL", "VMS_SQL_RUNNER_HOST").rstrip("/")
    user = conf("SQL_RUNNER_USER", "VMS_SQL_RUNNER_USERNAME")
    password = conf("SQL_RUNNER_PASSWORD", "VMS_SQL_RUNNER_PASSWORD")
    missing = [k for k, v in (("SQL_RUNNER_URL", url), ("SQL_RUNNER_USER", user), ("SQL_RUNNER_PASSWORD", password)) if not v]
    if missing:
        raise RunnerError(f"chưa cấu hình {', '.join(missing)} (thêm vào {dev_cli_env_path()})", 3)
    if not re.match(r"^https?://[^/\s]+", url):
        raise RunnerError("SQL_RUNNER_URL phải bắt đầu bằng http:// hoặc https://", 3)
    api = "/" + conf("SQL_RUNNER_API_PATH", default="/vms/api").strip("/")
    timeout = conf("SQL_RUNNER_TIMEOUT", default="60")
    return {"url": url, "api": api, "user": user, "password": password,
            "ca": os.path.expanduser(conf("SQL_RUNNER_CA_BUNDLE")),
            "timeout": int(timeout) if timeout.isdigit() else 60}


# ------------------------------------------------------------------ SQL checks

def strip_comments(sql):
    """SQL without comments; quotes kept. Raises on an unterminated quote or comment."""
    outp, i, n = [], 0, len(sql)
    while i < n:
        c = sql[i]
        if c in "'\"`":
            j = i + 1
            while j < n:
                if sql[j] == "\\" and c != "`":
                    j += 2
                    continue
                if sql[j] == c:
                    if j + 1 < n and sql[j + 1] == c:
                        j += 2
                        continue
                    break
                j += 1
            if j >= n:
                raise RunnerError(f"dấu {c} chưa đóng trong câu lệnh")
            outp.append(sql[i:j + 1])
            i = j + 1
        elif sql.startswith("/*", i):
            j = sql.find("*/", i + 2)
            if j < 0:
                raise RunnerError("chú thích /* chưa đóng")
            outp.append(" ")
            i = j + 2
        elif sql.startswith("--", i) and (i + 2 >= n or sql[i + 2] in " \t\r\n") or c == "#":
            j = sql.find("\n", i)
            i = n if j < 0 else j
        else:
            outp.append(c)
            i += 1
    return "".join(outp)


def split_statements(sql):
    text = strip_comments(sql)
    parts, buf, quote = [], [], None
    for i, c in enumerate(text):
        if quote:
            buf.append(c)
            if c == quote and not (quote != "`" and i > 0 and text[i - 1] == "\\"):
                quote = None
        elif c in "'\"`":
            quote = c
            buf.append(c)
        elif c == ";":
            parts.append("".join(buf).strip())
            buf = []
        else:
            buf.append(c)
    parts.append("".join(buf).strip())
    return [p for p in parts if p]


def classify(stmt):
    """-> 'read', 'ddl', 'dml' or 'other'."""
    first = re.match(r"\s*\(?\s*([A-Za-z]+)", stmt)
    word = first.group(1).lower() if first else ""
    if word in READ_KEYWORDS:
        return "write-read" if READ_BUT_WRITE_RE.search(stmt) else "read"
    if word in DDL_KEYWORDS:
        return "ddl"
    if word in DML_KEYWORDS:
        return "dml"
    return "other"


def lint(stmt):
    """-> [(level, message)] for statements the tool is known to reject; level 'block' stops the send."""
    issues = []
    low = re.sub(r"\s+", " ", stmt.lower())
    if re.match(r"\s*drop\s+(table|view|database|schema|index|trigger|procedure|function)\b", low):
        issues.append(("block", "DROP bị công cụ chặn phía server (403 'DDL operation DROP is not allowed'); "
                                "cần quyền DB trực tiếp ngoài công cụ"))
    if re.match(r"\s*create\s+(or\s+replace\s+)?sequence\b", low):
        issues.append(("block", "CREATE SEQUENCE không được hỗ trợ (500 'Finding tables from CreateSequence is not "
                                "supported'); cần quyền DB trực tiếp, hoặc để framework của service tự tạo"))
    if re.search(r"\bdefiner\s*=|\bsql\s+security\b|\balgorithm\s*=", low) and re.match(r"\s*(create|alter)\b", low):
        issues.append(("block", "bỏ DEFINER=/SQL SECURITY/ALGORITHM=: bộ phân tích của công cụ không xác định được "
                                "bảng (403). Dùng `CREATE OR REPLACE VIEW tên AS SELECT ...` (view chạy với quyền "
                                "của user kết nối)"))
    if re.match(r"\s*create\s+(temporary\s+)?table\b", low) and re.search(r"\)\s*[^()]*\bpartition\s+by\b", low):
        issues.append(("block", "tách PARTITION BY khỏi CREATE TABLE: chạy `CREATE TABLE` không partition, rồi "
                                "`ALTER TABLE ... PARTITION BY ...` riêng"))
    if re.search(r"'(?:[^'\\]|\\.|'')*\\'(?:[^'\\]|\\.|'')*'", stmt):
        issues.append(("block", "dấu nháy thoát bằng \\' làm công cụ không xác định được bảng (403): dùng hai dấu "
                                "nháy '' (vd 'can''t')"))
    if re.search(r"partition\s+by\s+range\s+columns", low) and re.search(r"\btimestamp\b", low):
        issues.append(("warn", "MariaDB không cho RANGE COLUMNS trên cột TIMESTAMP: cột partition nên là DATETIME(3) "
                               "(kiểm code có dựa vào chuyển đổi UTC của TIMESTAMP không)"))
    m = re.search(r"\b(?:drop|modify|change|add)\s+column\s+(type|key|status|order|group|level|value|name|date|time)\b",
                  stmt, re.I)
    if m:
        issues.append(("warn", f"tên cột `{m.group(1)}` trùng từ khóa có thể làm công cụ báo 403: đặt trong dấu "
                               f"backtick (`{m.group(1)}`)"))
    return issues


def globs(raw):
    return [g.strip().lower() for g in raw.split(",") if g.strip()]


PROD_WORDS = {"prod", "prd", "production", "live", "dr", "standby"}
MYSQL_TYPES = {"mysql", "mariadb"}


def is_prod(name):
    pattern = conf("SQL_RUNNER_PROD_PATTERN")
    if pattern:
        return any(fnmatch.fnmatch(name.strip().lower(), g) for g in globs(pattern))
    return bool(PROD_WORDS & set(re.split(r"[^a-z0-9]+", name.lower())))


def write_allowed(name):
    allow = globs(conf("SQL_RUNNER_WRITE_CONNECTIONS"))
    return bool(allow) and any(fnmatch.fnmatch(name.strip().lower(), g) for g in allow)


# ------------------------------------------------------------------ HTTP

class Client:
    def __init__(self, cfg):
        self.cfg = cfg
        self.token = None
        if cfg["ca"]:
            if not os.path.isfile(cfg["ca"]):
                raise RunnerError(f"SQL_RUNNER_CA_BUNDLE không có file: {cfg['ca']}", 3)
            self.ctx = ssl.create_default_context(cafile=cfg["ca"])
        else:
            self.ctx = ssl.create_default_context()

    def call(self, method, path, body=None):
        url = self.cfg["url"] + self.cfg["api"] + path
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Accept", "application/json")
        if data is not None:
            req.add_header("Content-Type", "application/json")
        if self.token:
            req.add_header("Authorization", f"Bearer {self.token}")
        try:
            with urllib.request.urlopen(req, timeout=self.cfg["timeout"], context=self.ctx) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            payload = self._json(raw)
            msg = payload.get("msg") or payload.get("message") if isinstance(payload, dict) else None
            raise RunnerError(f"HTTP {exc.code}: {self._mask(msg or raw[:300].decode('utf-8', 'replace'))}", 6) from None
        except urllib.error.URLError as exc:
            reason = exc.reason
            if isinstance(reason, ssl.SSLError):
                raise RunnerError(f"lỗi TLS: {reason} (CA nội bộ → SQL_RUNNER_CA_BUNDLE)", 5) from None
            raise RunnerError(f"không kết nối được: {reason}", 5) from None
        except (TimeoutError, OSError) as exc:
            raise RunnerError(f"không kết nối được: {exc}", 5) from None
        payload = self._json(raw)
        if isinstance(payload, dict):
            status = payload.get("status")
            if isinstance(status, int) and status >= 400:
                raise RunnerError(f"{status}: {self._mask(str(payload.get('msg') or payload.get('message') or ''))}", 6)
        return payload

    def _json(self, raw):
        try:
            return json.loads(raw.decode("utf-8", "replace"))
        except ValueError:
            return {}

    def _mask(self, text):
        for secret in (self.cfg["password"], self.token):
            if secret:
                text = text.replace(secret, "***")
        return text

    def login(self):
        payload = self.call("POST", "/auth/login", {"username": self.cfg["user"], "password": self.cfg["password"]})
        token = (payload.get("data") or {}).get("token") if isinstance(payload, dict) else None
        if not token:
            raise RunnerError("đăng nhập không thành công (không có token trong phản hồi)", 6)
        self.token = token

    def connections(self):
        payload = self.call("GET", "/wa/connection-info/get-all-public")
        data = payload.get("data") if isinstance(payload, dict) else payload
        return [c for c in (data or []) if isinstance(c, dict)]

    def connection(self, name):
        conns = self.connections()
        for exact in (True, False):
            for c in conns:
                cname = str(c.get("name"))
                if cname == name if exact else cname.strip().lower() == name.strip().lower():
                    return c
        near = [str(c.get("name")).strip() for c in conns if name.strip().lower()[:6] in str(c.get("name")).lower()]
        hint = f" Gần giống: {', '.join(sorted(near)[:15])}." if near else ""
        raise RunnerError(f"không có connection `{name}`.{hint} Xem: sql_runner.py connections --filter <chữ>", 4)

    def connection_id(self, name):
        return str(self.connection(name).get("id"))

    def execute(self, conn_id, sql, affected=None):
        body = {"query": sql, "database": {"id": conn_id, "name": ""}}
        if affected is not None:
            body["affectedRows"] = affected
        payload = self.call("POST", "/wa/sql-runner/execute", body)
        return (payload.get("data") if isinstance(payload, dict) else None) or {}


# ------------------------------------------------------------------ output

def table_of(data):
    cols = data.get("columns") or []
    names = [c if isinstance(c, str) else str(c.get("name") or c.get("label") or c.get("columnName") or "?")
             for c in cols]
    rows = []
    for r in data.get("rows") or []:
        if isinstance(r, dict):
            if not names:
                names = list(r)
            rows.append([r.get(n) for n in names])
        elif isinstance(r, (list, tuple)):
            rows.append(list(r))
        else:
            rows.append([r])
    return names, rows


def cell(v, width):
    s = "NULL" if v is None else str(v)
    s = re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", "", s).replace("\n", "\\n").replace("\t", " ")
    return s if width <= 0 or len(s) <= width else s[:width - 1] + "…"


def render(names, rows, fmt, max_rows, width):
    shown = rows[:max_rows] if max_rows > 0 else rows
    if fmt == "json":
        return json.dumps([dict(zip(names, r)) for r in shown], ensure_ascii=False, default=str, indent=1)
    if fmt == "csv":
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(names)
        w.writerows(shown)
        return buf.getvalue().rstrip("\n")
    cells = [[cell(v, width) for v in r] for r in shown]
    widths = [max([len(n)] + [len(r[i]) for r in cells if i < len(r)]) for i, n in enumerate(names)]
    lines = [" | ".join(n.ljust(widths[i]) for i, n in enumerate(names)),
             "-+-".join("-" * w for w in widths)]
    lines += [" | ".join(v.ljust(widths[i]) if i < len(widths) else v for i, v in enumerate(r)) for r in cells]
    return "\n".join(lines)


# ------------------------------------------------------------------ commands

def connection_name(args):
    name = args.conn or conf("SQL_RUNNER_CONNECTION")
    if not name:
        raise RunnerError("thiếu --conn <tên connection> (xem: sql_runner.py connections), hoặc đặt SQL_RUNNER_CONNECTION")
    return name


def session(args):
    client = Client(config())
    client.login()
    return client


def cmd_doctor(args):
    cfg = config()
    src = setting("SQL_RUNNER_URL")[1] if setting("SQL_RUNNER_URL")[0] else "VMS_SQL_RUNNER_HOST"
    out(f"settings: {dev_cli_env_path()}  (url from {src})")
    out(f"url: {cfg['url']}{cfg['api']}")
    if cfg["url"].startswith("http://"):
        out("warning: http:// — mật khẩu và kết quả truy vấn đi qua mạng không mã hóa")
    out(f"user: {cfg['user']}   password: set")
    out(f"ca bundle: {cfg['ca'] or '(mặc định của hệ thống)'}")
    out(f"default connection: {conf('SQL_RUNNER_CONNECTION') or '(chưa đặt SQL_RUNNER_CONNECTION: phải ghi --conn mỗi lần)'}")
    out(f"write connections: {conf('SQL_RUNNER_WRITE_CONNECTIONS') or '(chưa đặt SQL_RUNNER_WRITE_CONNECTIONS: mọi lệnh ghi bị từ chối)'}")
    out(f"production pattern: {conf('SQL_RUNNER_PROD_PATTERN') or 'tên có từ prod/prd/production/live/dr/standby'}")
    client = Client(cfg)
    client.login()
    out("login: ok")
    conns = client.connections()
    out(f"connections: {len(conns)}")
    default = conf("SQL_RUNNER_CONNECTION")
    if default:
        try:
            c = client.connection(default)
            out(f"default connection `{default}`: có ({c.get('dbType') or '?'})")
        except RunnerError as exc:
            out(f"warning: {exc}")
    return 0


def cmd_connections(args):
    client = session(args)
    rows = []
    for c in client.connections():
        name, kind = str(c.get("name")), str(c.get("dbType") or c.get("type") or "")
        if args.filter and args.filter.lower() not in name.lower():
            continue
        if args.type and args.type.lower() != kind.lower():
            continue
        writes = "ghi được" if write_allowed(name) and not is_prod(name) else ""
        rows.append([name.strip(), kind, "production" if is_prod(name) else "", writes])
    rows.sort(key=lambda r: r[0].lower())
    out(render(["name", "type", "class", "writes"], rows, "table", 0, 60))
    out(f"({len(rows)} connection)")
    return 0


def run_reads(client, conn_id, statements, args):
    for n, sql in enumerate(statements):
        if len(statements) > 1:
            out(f"--- {n + 1}/{len(statements)}: {cell(sql, 160)}")
        data = client.execute(conn_id, sql)
        names, rows = table_of(data)
        if names:
            text = render(names, rows, args.format, args.max_rows, args.width)
            if args.out:
                if os.path.exists(args.out):
                    raise RunnerError(f"đã có file {args.out}, không ghi đè", 2)
                with open(args.out, "w", encoding="utf-8", newline="") as fh:
                    fh.write(render(names, rows, args.format if args.format != "table" else "csv", 0, 0) + "\n")
                os.chmod(args.out, 0o600)
                out(f"{len(rows)} dòng → {args.out}")
            else:
                out(text)
                if args.max_rows > 0 and len(rows) > args.max_rows:
                    out(f"... {len(rows)} dòng, mới in {args.max_rows} (thêm LIMIT/WHERE, hoặc --out file)")
                else:
                    out(f"({len(rows)} dòng)")
        else:
            out(str(data.get("message") or "ok"))


def cmd_query(args):
    if args.file:
        with open(args.file, encoding="utf-8") as fh:
            sources = [fh.read()]
    else:
        sources = args.sql
    if not sources:
        raise RunnerError("thiếu câu lệnh (đối số, hoặc --file)")
    statements = []
    for src in sources:
        parts = split_statements(src)
        if len(parts) > 1:
            raise RunnerError(f"một đối số chứa {len(parts)} câu lệnh: tách mỗi câu một đối số (đọc) hoặc một lần chạy (ghi)")
        statements += parts
    if not statements:
        raise RunnerError("câu lệnh rỗng")
    kinds = [classify(s) for s in statements]
    writes = [s for s, k in zip(statements, kinds) if k != "read"]
    name = connection_name(args)
    if not writes:
        client = session(args)
        run_reads(client, client.connection_id(name), statements, args)
        return 0
    if len(statements) > 1:
        raise RunnerError("câu lệnh ghi phải chạy riêng, mỗi lần một câu")
    sql, kind = statements[0], kinds[0]
    if not args.write:
        raise RunnerError(f"câu lệnh {kind.upper()} sẽ thay đổi dữ liệu hoặc cấu trúc: chế độ mặc định chỉ đọc. "
                          "Chạy lại với --write khi người dùng đã đồng ý đúng câu lệnh này")
    problems = lint(sql)
    for level, msg in problems:
        print(f"{'chặn' if level == 'block' else 'lưu ý'}: {msg}", file=sys.stderr)
    if any(level == "block" for level, _ in problems) and not args.skip_lint:
        raise RunnerError("không gửi: câu lệnh có mẫu mà công cụ từ chối (sửa theo gợi ý ở trên)")
    if not write_allowed(name):
        allow = conf("SQL_RUNNER_WRITE_CONNECTIONS")
        raise RunnerError(f"`{name}` không nằm trong SQL_RUNNER_WRITE_CONNECTIONS ({allow})" if allow else
                          "chưa đặt SQL_RUNNER_WRITE_CONNECTIONS (vd uat_*) trong ~/.dev-cli/.env: mọi lệnh ghi bị từ chối")
    if is_prod(name) and not args.allow_prod:
        raise RunnerError(f"`{name}` là connection production (SQL_RUNNER_PROD_PATTERN): không ghi nếu không có "
                          "--allow-prod và yêu cầu rõ của người dùng")
    affected = args.affected_rows
    if kind == "dml" and affected is None:
        raise RunnerError("câu lệnh DML cần --affected-rows N (công cụ rollback khi số dòng khác N). Đếm trước bằng "
                          "SELECT COUNT(*) với cùng điều kiện WHERE, hoặc số bộ VALUES của INSERT")
    plan = f"{kind.upper()} trên `{name}`" + (f", affectedRows={affected}" if affected is not None else "")
    code = confirm_code(name, sql, affected, kind)
    if (args.confirm or "").strip().upper() != code:
        if args.confirm:
            print("sql_runner: mã xác nhận không khớp câu lệnh này (câu lệnh, connection hoặc số dòng đã đổi): "
                  "chưa chạy", file=sys.stderr)
        out("CHƯA CHẠY. Bước xác nhận: người dùng đọc kế hoạch dưới đây và tự gõ lại mã xác nhận.")
        out(f"kế hoạch: {plan}")
        out("câu lệnh:")
        out(sql)
        out(f"mã xác nhận: {code}")
        return 2
    client = session(args)
    conn_id = client.connection_id(name)
    data = client.execute(conn_id, sql, affected)
    got = data.get("affectedRows")
    out(f"đã chạy: {plan}" + (f"; số dòng bị ảnh hưởng: {got}" if got is not None else ""))
    if data.get("message"):
        out(str(data["message"]))
    names, rows = table_of(data)
    if names:
        out(render(names, rows, "table", args.max_rows, args.width))
    return 0


def confirm_code(name, sql, affected, kind):
    """Six characters bound to this exact connection, statement and row count."""
    raw = "\n".join((name.strip().lower(), kind, re.sub(r"\s+", " ", sql.strip()), str(affected)))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:6].upper()


def quoted(ident):
    if not IDENT_RE.match(ident):
        raise RunnerError(f"tên không hợp lệ: {ident} (chỉ chữ, số, _ và $)")
    return f"`{ident}`"


def require_mysql(args):
    client = session(args)
    c = client.connection(connection_name(args))
    kind = str(c.get("dbType") or "").lower()
    if kind and kind not in MYSQL_TYPES:
        raise RunnerError(f"`{c.get('name')}` là {kind}: schemas/tables/describe chỉ dành cho MySQL/MariaDB; "
                          "dùng query với câu lệnh của hệ đó (vd Oracle: select table_name from all_tables where owner = '...')")


def cmd_schemas(args):
    require_mysql(args)
    args.sql = ["select schema_name from information_schema.schemata order by schema_name"]
    return cmd_query(args)


def cmd_tables(args):
    quoted(args.schema)
    require_mysql(args)
    args.sql = ["select table_name, table_type, table_rows, round((data_length+index_length)/1048576, 1) as mb "
                f"from information_schema.tables where table_schema = '{args.schema}' order by table_name"]
    return cmd_query(args)


def cmd_describe(args):
    schema, _, table = args.target.partition(".")
    if not table:
        raise RunnerError("ghi dạng SCHEMA.TABLE")
    args.sql = [f"show create table {quoted(schema)}.{quoted(table)}"]
    require_mysql(args)
    args.width = 0
    return cmd_query(args)


def cmd_lint(args):
    sql = open(args.file, encoding="utf-8").read() if args.file else " ".join(args.sql)
    parts = split_statements(sql)
    worst = 0
    for s in parts:
        kind = classify(s)
        issues = lint(s) if kind != "read" else []
        out(f"{kind.upper()}: {cell(s, 120)}")
        for level, msg in issues:
            out(f"  {'chặn' if level == 'block' else 'lưu ý'}: {msg}")
            worst = 2 if level == "block" else worst
        if not issues:
            out("  ok")
    if len(parts) > 1:
        out(f"{len(parts)} câu lệnh: gửi từng câu một")
    return worst


def main(argv=None):
    parser = argparse.ArgumentParser(prog="sql_runner.py", description="Run SQL through a web SQL Runner tool.")
    sub = parser.add_subparsers(dest="command", required=True)

    def common(p):
        p.add_argument("--conn", help="connection name (SQL_RUNNER_CONNECTION by default)")
        p.add_argument("--max-rows", type=int, default=200, help="rows printed (0 = all; default 200)")
        p.add_argument("--width", type=int, default=80, help="cell width in table output (0 = full)")
        p.add_argument("--format", choices=("table", "json", "csv"), default="table")
        p.add_argument("--out", help="write the rows to this file (csv/json, mode 0600, never overwritten)")
        p.set_defaults(write=False, confirm=None, allow_prod=False, skip_lint=False, affected_rows=None, file=None)

    sub.add_parser("doctor").set_defaults(func=cmd_doctor)
    p = sub.add_parser("connections")
    p.add_argument("--filter", help="part of the name, case-insensitive")
    p.add_argument("--type", help="mysql, mariadb, oracle, mongodb, ...")
    p.set_defaults(func=cmd_connections)
    p = sub.add_parser("schemas")
    common(p)
    p.set_defaults(func=cmd_schemas)
    p = sub.add_parser("tables")
    p.add_argument("schema")
    common(p)
    p.set_defaults(func=cmd_tables)
    p = sub.add_parser("describe")
    p.add_argument("target", metavar="SCHEMA.TABLE")
    common(p)
    p.set_defaults(func=cmd_describe)
    p = sub.add_parser("query")
    p.add_argument("sql", nargs="*")
    common(p)
    p.add_argument("--file", help="one statement from a .sql file (long DDL, quotes, backticks)")
    p.add_argument("--write", action="store_true", help="allow a statement that changes data or schema")
    p.add_argument("--affected-rows", type=int, help="rows the write will change (required for INSERT/UPDATE/DELETE)")
    p.add_argument("--confirm", metavar="CODE", help="confirmation code printed by the dry run, typed back by the user")
    p.add_argument("--allow-prod", action="store_true", help="write on a production connection")
    p.add_argument("--skip-lint", action="store_true", help="send even when lint would block")
    p.set_defaults(func=cmd_query)
    p = sub.add_parser("lint")
    p.add_argument("sql", nargs="*")
    p.add_argument("--file")
    p.set_defaults(func=cmd_lint)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except RunnerError as exc:
        print(f"sql_runner: {exc}", file=sys.stderr)
        return exc.code
    except OSError as exc:
        print(f"sql_runner: {exc.strerror or exc}: {getattr(exc, 'filename', '') or ''}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
