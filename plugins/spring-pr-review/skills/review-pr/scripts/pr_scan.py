#!/usr/bin/env python3
"""Scan a git range for a Spring Boot pull-request review: commits, file categories, risk findings.

Reads the repository with git rev-parse, merge-base, log, rev-list, diff, show, ls-tree and grep.
Never checks out, fetches, merges or changes a branch, the index or the working tree. The merge
check (git merge-tree --write-tree) stores the trial merge's objects in .git/objects, unreferenced;
git gc removes them. --no-merge-check skips it.

Secret values are never printed: a secret-looking key is reported by its name and line number only,
SQL by the rule it matched. Read the file itself when the context matters.

Examples:
  python3 pr_scan.py --base origin/staging --head origin/feature/x
  python3 pr_scan.py --repo ~/work/service --base origin/release --head origin/sync --pr 13 --format text
  python3 pr_scan.py --base origin/staging --head HEAD --patch-file "$TMP/pr.patch"

Exit codes: 0 ok, 2 usage or git error.
"""

import argparse
import datetime
import json
import os
import re
import subprocess
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from plugin_settings import setting  # noqa: E402

SEVERITY_ORDER = {"high": 0, "medium": 1, "info": 2}
COMMIT_FORMAT = "%H%x1f%h%x1f%an%x1f%ad%x1f%s%x1f%b%x1e"
DIALECTS = ("mariadb", "mysql", "postgresql", "oracle", "sqlserver", "unknown")

# ---------------------------------------------------------------- file categories

MIGRATION_DIR_RE = re.compile(
    r"(^|/)(db/migrations?|db/changelogs?|db/changesets?|migrations?|liquibase|flyway|changelogs?)/"
    r"(.*/)?[^/]+\.(sql|ya?ml|xml|json)$",
    re.I,
)
FLYWAY_RE = re.compile(r"(^|/)V(?P<version>\d+(?:[._]\d+)*)__[^/]*\.(sql|java)$")
FLYWAY_OTHER_RE = re.compile(r"(^|/)[UR]\d*[._\d]*__[^/]*\.sql$")
CHANGELOG_NAME_RE = re.compile(r"(^|/)(db\.)?changelog[\w.-]*\.(ya?ml|xml|json|sql)$", re.I)
CHANGELOG_INDEX_NAME_RE = re.compile(
    r"(^|/)((db\.)?changelog([-_.](master|main|root|index))?|master)\.(ya?ml|xml|json)$", re.I
)
CONFIG_RE = re.compile(
    r"(^|/)(application|bootstrap)([-.][\w-]+)?\.(ya?ml|properties)$|(^|/)config/[^/]+\.(ya?ml|properties)$",
    re.I,
)
DEPLOY_RE = re.compile(
    r"(^|/)(helm|charts?|k8s|kubernetes|deploy|deployment|manifests?)/"
    r"|(^|/)(values[\w.-]*|docker-compose[\w.-]*)\.ya?ml$"
    r"|(^|/)\.env(\.[\w-]+)?$|(^|/)Dockerfile[\w.-]*$",
    re.I,
)
BUILD_RE = re.compile(
    r"(^|/)(pom\.xml|build\.gradle(\.kts)?|settings\.gradle(\.kts)?|gradle\.properties|libs\.versions\.toml)$",
    re.I,
)
TEST_RE = re.compile(r"(^|/)src/test/", re.I)
SECURITY_RE = re.compile(
    r"(^|/)(security|auth|oauth2?|jwt|keycloak)/"
    r"|(^|/)\w*(Security|Authentication|Authorization|Auth(?!or)|Jwt|OAuth2?|Keycloak)\w*\.java$"
    r"|(^|/)\w*Filter\.java$"
)
API_RE = re.compile(r"(^|/)(controllers?|api|rest|web)/[^/]*\.java$|(Controller|Resource|Endpoint)\.java$")
PERSIST_RE = re.compile(
    r"(^|/)(entity|entities|persistence|repository|repositories|dao)/|(Repository|Entity|Dao)\.java$"
)
CONFIG_TEXT_RE = re.compile(r"\.(ya?ml|properties)$|(^|/)\.env(\.[\w-]+)?$", re.I)


def categorize(path):
    cats = []
    if MIGRATION_DIR_RE.search(path) or FLYWAY_RE.search(path) or FLYWAY_OTHER_RE.search(path) \
            or CHANGELOG_NAME_RE.search(path):
        cats.append("migration")
    if CONFIG_RE.search(path):
        cats.append("config")
    if DEPLOY_RE.search(path):
        cats.append("deploy")
    if BUILD_RE.search(path):
        cats.append("build")
    if TEST_RE.search(path):
        cats.append("test")
    elif path.endswith(".java"):
        cats.append("source")
        if SECURITY_RE.search(path):
            cats.append("security")
        if API_RE.search(path):
            cats.append("api")
        if PERSIST_RE.search(path):
            cats.append("persistence")
    if not cats:
        cats.append("other")
    return cats


# ---------------------------------------------------------------- SQL and Liquibase rules

def _rx(pattern):
    return re.compile(pattern, re.I)


#: (severity, kind, pattern, detail, unless-pattern or None). Run on added lines of migration files,
#: after quoted strings and comments are removed. Output names the rule, never the statement.
COMMON_SQL_RULES = [
    ("high", "sql-drop", _rx(r"\bdrop\s+(table|column|schema|database|view|sequence|primary\s+key)\b"),
     "DROP bảng/cột: mất dữ liệu; xác nhận không còn code đọc", None),
    ("medium", "sql-drop-index", _rx(r"\bdrop\s+(index|constraint|key|foreign\s+key)\b"),
     "bỏ index/ràng buộc: kiểm tra ràng buộc thay thế đã có (cùng điều kiện DB) và hiệu năng truy vấn", None),
    ("high", "sql-truncate", _rx(r"\btruncate\b"), "TRUNCATE: xóa toàn bộ dữ liệu bảng", None),
    ("high", "sql-rename", _rx(r"\brename\s+(to|column|table)\b"),
     "RENAME: code bản cũ đang chạy song song sẽ lỗi trong lúc deploy", None),
    ("high", "sql-type-change",
     _rx(r"\bset\s+data\s+type\b|\balter\s+column\s+\S+\s+type\b"
         r"|\bmodify\s+(column\s+)?\(?[`\"\w.]+\s+\w+"
         r"|\bchange\s+(column\s+)?[`\"\w.]+\s+[`\"\w.]+\s+\w+"),
     "đổi kiểu hoặc định nghĩa cột (ALTER COLUMN TYPE / MODIFY / CHANGE): có thể mất dữ liệu, dựng lại bảng", None),
    ("medium", "sql-not-null", _rx(r"\bset\s+not\s+null\b"),
     "NOT NULL trên cột đã có: lỗi nếu còn dòng NULL", None),
    ("medium", "sql-add-not-null",
     _rx(r"\badd\s+(column\s+)?(if\s+not\s+exists\s+)?[`\"\w.]+\s+[^,;]*\bnot\s+null\b(?![^,;]*\bdefault\b)"),
     "cột NOT NULL mới không có DEFAULT: lỗi nếu bảng đã có dữ liệu", _rx(r"\bdefault\b")),
    ("medium", "sql-drop-not-null", _rx(r"\bdrop\s+not\s+null\b"),
     "bỏ NOT NULL: code đọc cột phải chịu được NULL", None),
    ("medium", "sql-unique",
     _rx(r"\bcreate\s+unique\s+index\b|\badd\s+(constraint\s+\S+\s+)?unique\b"),
     "ràng buộc UNIQUE mới: migration gãy nếu dữ liệu đang trùng; chạy câu kiểm trùng trước", None),
    ("medium", "sql-delete-no-where", _rx(r"\bdelete\s+from\b(?![^;]*\bwhere\b)"),
     "DELETE không thấy WHERE trên dòng này: kiểm tra phạm vi", None),
    ("medium", "sql-update-no-where", _rx(r"\bupdate\s+\S+\s+set\b(?![^;]*\bwhere\b)"),
     "UPDATE không thấy WHERE trên dòng này: kiểm tra phạm vi", None),
]

INDEX_RX = _rx(r"\bcreate\s+(unique\s+)?index\b|\badd\s+(unique\s+|fulltext\s+|spatial\s+)?(index|key)\b")

DIALECT_SQL_RULES = {
    "postgresql": [
        ("medium", "sql-index-lock", _rx(r"\bcreate\s+(unique\s+)?index\b"),
         "CREATE INDEX không CONCURRENTLY: khóa ghi bảng trong lúc tạo (PostgreSQL)", _rx(r"\bconcurrently\b")),
    ],
    "mariadb": [
        ("medium", "sql-generated-column", _rx(r"\bas\s*\(.*\)\s*(persistent|stored)\b"),
         "cột sinh PERSISTENT/STORED: MariaDB/MySQL dựng lại toàn bộ bảng; cần cửa sổ bảo trì", None),
        ("info", "sql-index-lock", INDEX_RX,
         "tạo index trên bảng lớn: chạy lâu và chờ metadata lock (MariaDB/MySQL); chạy ngoài giờ cao điểm",
         _rx(r"\balgorithm\s*=|\block\s*=")),
    ],
    "unknown": [
        ("info", "sql-index-lock", INDEX_RX, "tạo index trên bảng lớn có thể khóa hoặc chạy lâu: kiểm tra theo DB",
         _rx(r"\bconcurrently\b|\balgorithm\s*=")),
    ],
}
DIALECT_SQL_RULES["mysql"] = DIALECT_SQL_RULES["mariadb"]
DIALECT_SQL_RULES["oracle"] = DIALECT_SQL_RULES["unknown"]
DIALECT_SQL_RULES["sqlserver"] = DIALECT_SQL_RULES["unknown"]

SQL_STATEMENTS = [
    ("create table", _rx(r"\bcreate\s+table\b")),
    ("alter table", _rx(r"\balter\s+table\b")),
    ("create index", _rx(r"\bcreate\s+(unique\s+)?index\b")),
    ("insert", _rx(r"\binsert\s+into\b")),
    ("update", _rx(r"\bupdate\s+\S+\s+set\b")),
    ("delete", _rx(r"\bdelete\s+from\b")),
    ("drop", _rx(r"\bdrop\s+(table|column|index|constraint|schema|database|view|sequence)\b")),
]

#: Liquibase change types (YAML key, XML element or JSON key) -> (severity, kind, detail).
LIQUIBASE_RULES = {
    "dropTable": ("high", "lb-drop", "dropTable: mất bảng và dữ liệu; xác nhận không còn code đọc"),
    "dropColumn": ("high", "lb-drop", "dropColumn: mất cột và dữ liệu; code bản cũ đang chạy sẽ lỗi"),
    "dropView": ("high", "lb-drop", "dropView: kiểm tra còn ai đọc view"),
    "dropSequence": ("high", "lb-drop", "dropSequence: kiểm tra còn ai dùng sequence"),
    "dropPrimaryKey": ("high", "lb-drop", "dropPrimaryKey: mất khóa chính"),
    "renameTable": ("high", "lb-rename", "renameTable: code bản cũ đang chạy song song sẽ lỗi trong lúc deploy"),
    "renameColumn": ("high", "lb-rename", "renameColumn: code bản cũ đang chạy song song sẽ lỗi trong lúc deploy"),
    "renameView": ("high", "lb-rename", "renameView: code bản cũ sẽ lỗi"),
    "modifyDataType": ("high", "lb-type-change",
                       "modifyDataType: thu hẹp kiểu có thể mất dữ liệu, nới rộng thường an toàn; có thể dựng lại bảng"),
    "addNotNullConstraint": ("medium", "lb-not-null",
                             "addNotNullConstraint: lỗi nếu còn dòng NULL (cần defaultNullValue hoặc backfill)"),
    "dropNotNullConstraint": ("medium", "lb-drop-not-null", "dropNotNullConstraint: code đọc cột phải chịu được NULL"),
    "addUniqueConstraint": ("medium", "lb-unique", "addUniqueConstraint: migration gãy nếu dữ liệu đang trùng"),
    "dropUniqueConstraint": ("medium", "lb-drop-unique",
                             "dropUniqueConstraint: kiểm tra ràng buộc thay thế được tạo trên mọi DB (cùng dbms)"),
    "dropForeignKeyConstraint": ("medium", "lb-drop-fk", "dropForeignKeyConstraint: mất toàn vẹn tham chiếu"),
    "delete": ("medium", "lb-delete", "delete: xóa dữ liệu; kiểm tra điều kiện where"),
    "update": ("medium", "lb-update", "update: sửa dữ liệu hàng loạt; kiểm tra điều kiện where"),
    "createIndex": ("info", "lb-index", "createIndex: bảng lớn chạy lâu hoặc khóa"),
    "addForeignKeyConstraint": ("info", "lb-fk", "khóa ngoại mới: dữ liệu mồ côi làm migration gãy"),
    "addColumn": ("info", "lb-add-column", "addColumn: kiểm tra nullable/defaultValue"),
    "createTable": ("info", "lb-create-table", "createTable"),
    "sql": ("info", "lb-raw-sql", "SQL thuần trong changeset: đọc câu lệnh"),
    "sqlFile": ("info", "lb-raw-sql", "sqlFile: đọc file SQL được gọi"),
    "loadData": ("info", "lb-load-data", "loadData: nạp dữ liệu từ file"),
    "loadUpdateData": ("info", "lb-load-data", "loadUpdateData: nạp/ghi đè dữ liệu từ file"),
    "customChange": ("info", "lb-custom", "customChange: logic Java trong migration"),
}
#: Change types Liquibase cannot roll back by itself.
LIQUIBASE_NO_AUTO_ROLLBACK = {"dropTable", "dropColumn", "dropView", "dropSequence", "dropPrimaryKey",
                              "modifyDataType", "delete", "update", "sql", "sqlFile", "loadData",
                              "loadUpdateData", "dropUniqueConstraint", "dropForeignKeyConstraint", "customChange"}
LB_NAME_RES = (
    re.compile(r"^\s*-?\s*(\w+)\s*:\s*(?:#.*)?$"),   # YAML: a change type is a key with nested content
    re.compile(r"<\s*(\w+)[\s/>]"),          # XML
    re.compile(r"\"(\w+)\"\s*:"),            # JSON
)
INDEX_LINE_RE = re.compile(
    r"\b(include|includeAll)\b|\bfile[\"']?\s*[:=]|relativeToChangelogFile|databaseChangeLog|^\s*-?\s*$|^\s*(#|<!--|-->)",
    re.I,
)
INCLUDE_PATH_RE = re.compile(r"""\bfile["']?\s*[:=]\s*["']?([^"'\s>,}]+)""", re.I)

# ---------------------------------------------------------------- config rules

# Keys are matched by name only, not by full path (YAML nesting is not tracked on a diff).
YAML_KEY_RE = re.compile(r"^(\s*)-?\s*([A-Za-z0-9_.\-\[\]]+)\s*:(\s+(.*)|\s*)$")
PROPERTIES_KEY_RE = re.compile(r"^\s*(?:export\s+)?([A-Za-z0-9_.\-\[\]]+)\s*[=:]\s*(.*)$")
SECRET_KEY_RE = re.compile(r"(?i)password|passwd|secret|token|api[-_.]?key|private[-_.]?key|credential")
BLOCK_SCALARS = {"|", ">", "|-", ">-", "|+", ">+"}

# ---------------------------------------------------------------- Java rules

JAVA_RULES = [
    ("medium", "jpa-mapping",
     re.compile(r"@(Entity|Table|Column|JoinColumn|JoinTable|OneToOne|OneToMany|ManyToOne|ManyToMany|Id)\b"),
     "mapping JPA đổi: đối chiếu với migration"),
    ("medium", "transactional", re.compile(r"@Transactional\b"),
     "@Transactional: kiểm tra phạm vi transaction và gọi nội bộ"),
    ("medium", "api-mapping", re.compile(r"@(Get|Post|Put|Patch|Delete|Request)Mapping\b"),
     "mapping endpoint: kiểm tra tương thích với client"),
    ("medium", "eager-fetch", re.compile(r"FetchType\.EAGER\b"),
     "FetchType.EAGER: nguy cơ tải thừa hoặc N+1"),
    ("medium", "security-open", re.compile(r"permitAll\(\)|csrf\(\)\s*\.\s*disable\(\)|csrf\(\s*\w+\s*->\s*\w+\.disable\(\)\s*\)"),
     "mở quyền truy cập hoặc tắt CSRF: kiểm tra có chủ đích"),
    ("medium", "kafka-group-changed", re.compile(r"\bgroupId\s*=\s*\""),
     "groupId trong code: group mới chưa có offset sẽ đọc theo auto-offset-reset của môi trường"),
    ("info", "kafka-listener", re.compile(r"@KafkaListener\b"),
     "consumer Kafka: kiểm tra group, auto-offset-reset, concurrency và idempotent"),
    ("info", "async-entry",
     re.compile(r"@(Scheduled|Async|RabbitListener|JmsListener|EventListener|TransactionalEventListener)\b"),
     "điểm vào chạy nền hoặc bất đồng bộ: kiểm tra chạy nhiều instance"),
    ("info", "topic-hardcoded", re.compile(r"\b[A-Z_]*TOPIC[A-Z_]*\s*=\s*\"[^\"$]+\""),
     "tên topic hardcode trong code: không đổi được theo môi trường"),
    ("info", "soft-delete-filter", re.compile(r"@(SQLRestriction|Where|SQLDelete|FilterDef)\b"),
     "lọc/xóa mềm ở entity: native query không tự lọc, phải tự thêm điều kiện"),
    ("info", "native-query", re.compile(r"nativeQuery\s*=\s*true"),
     "native query: kiểm tra SQL, index, điều kiện xóa mềm"),
    ("info", "client-no-timeout", re.compile(r"new\s+RestTemplate\s*\(\s*\)|WebClient\.create\s*\(\s*\)"),
     "HTTP client không cấu hình timeout tường minh"),
    ("info", "broad-catch", re.compile(r"catch\s*\(\s*(final\s+)?(Exception|Throwable)\b"),
     "catch rộng"),
]
JAVA_REMOVED_KINDS = {"jpa-mapping", "transactional", "api-mapping", "async-entry", "kafka-listener"}

MAVEN_DEP_RE = re.compile(r"<artifactId>([^<]+)</artifactId>")
GRADLE_DEP_RE = re.compile(
    r"^\s*(implementation|api|compileOnly|runtimeOnly|testImplementation|annotationProcessor)\b\s*\(?\s*[\"']([^\"']+)"
)
HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")

#: Findings computed once per file over the whole range (they depend on the file's status on the
#: target branch); a commit that touches the file carries them as flags.
FILE_LEVEL_KINDS = {"migration-existing-changed", "lb-include-changed", "lb-include-moved", "lb-include-order",
                    "lb-not-included", "flyway-duplicate-version", "flyway-out-of-order"}


class ScanError(Exception):
    pass


def run_git(repo, args, check=True):
    proc = subprocess.run(
        ["git", "-C", repo, *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if check and proc.returncode != 0:
        first = (proc.stderr or "").strip().splitlines()[:1]
        raise ScanError(f"git {args[0]} lỗi: {first[0] if first else 'exit ' + str(proc.returncode)}")
    return proc.stdout


def resolve(repo, ref):
    out = run_git(repo, ["rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"], check=False).strip()
    if not out:
        raise ScanError(f"không tìm thấy ref: {ref} (thử git fetch trước)")
    return out


def finding(severity, kind, path, line, detail):
    return {"severity": severity, "kind": kind, "file": path, "line": line, "detail": detail}


# ---------------------------------------------------------------- patch parsing

def parse_patch(text):
    """Return [{path, added: [(new_line_no, text)], removed: [text]}] from a unified diff with -U0."""
    files = []
    cur = None
    in_hunk = False
    new_no = 0
    for raw in text.splitlines():
        if raw.startswith("diff --git "):
            cur = {"path": None, "added": [], "removed": []}
            files.append(cur)
            in_hunk = False
            m = re.match(r"diff --git a/(.*) b/(.*)$", raw)
            if m:
                cur["path"] = m.group(2)
            continue
        if cur is None:
            continue
        if not in_hunk:
            if raw.startswith("+++ b/"):
                cur["path"] = raw[6:]
            elif raw.startswith("@@"):
                in_hunk = True
                m = HUNK_RE.match(raw)
                new_no = int(m.group(1)) if m else 0
            continue
        if raw.startswith("@@"):
            m = HUNK_RE.match(raw)
            new_no = int(m.group(1)) if m else 0
        elif raw.startswith("+"):
            cur["added"].append((new_no, raw[1:]))
            new_no += 1
        elif raw.startswith("-"):
            cur["removed"].append(raw[1:])
    return [f for f in files if f["path"]]


def parse_name_status(text):
    status = {}
    for line in text.splitlines():
        parts = line.split("\t")
        if len(parts) >= 2:
            status[parts[-1]] = parts[0][:1]
    return status


# ---------------------------------------------------------------- dialect

DIALECT_PATTERNS = [
    ("mariadb", re.compile(r"jdbc:mariadb|mariadb-java-client|org\.mariadb", re.I)),
    ("mysql", re.compile(r"jdbc:mysql|mysql-connector", re.I)),
    ("postgresql", re.compile(r"jdbc:postgresql|org\.postgresql|<artifactId>postgresql<", re.I)),
    ("oracle", re.compile(r"jdbc:oracle|ojdbc", re.I)),
    ("sqlserver", re.compile(r"jdbc:sqlserver|mssql-jdbc", re.I)),
]


def detect_dialect(repo, rev):
    out = run_git(repo, [
        "grep", "-I", "-i", "-h", "-E",
        r"jdbc:(mariadb|mysql|postgresql|oracle|sqlserver)|mariadb-java-client|org\.mariadb|mysql-connector"
        r"|org\.postgresql|<artifactId>postgresql<|ojdbc|mssql-jdbc",
        rev, "--", "*.xml", "*.gradle", "*.kts", "*.toml", "*.properties", "*.yml", "*.yaml", "*.env",
    ], check=False)
    counts = Counter()
    for line in out.splitlines():
        for name, rx in DIALECT_PATTERNS:
            if rx.search(line):
                counts[name] += 1
    if not counts:
        return "unknown"
    # The MariaDB driver also takes jdbc:mysql URLs: MariaDB wins a tie.
    order = {name: i for i, (name, _) in enumerate(DIALECT_PATTERNS)}
    return sorted(counts.items(), key=lambda kv: (-kv[1], order[kv[0]]))[0][0]


# ---------------------------------------------------------------- per-file analysis

def strip_sql(text):
    """Remove quoted strings and comments so data or remarks do not trigger SQL rules."""
    text = re.sub(r"'(?:[^'\\]|\\.|'')*'", "''", text)
    return re.sub(r"(--|#).*$", "", text)


def liquibase_names(text):
    names = []
    for rx in LB_NAME_RES:
        names.extend(m.group(1) for m in rx.finditer(text))
    return [n for n in names if n in LIQUIBASE_RULES]


def is_changelog_index(path, added, removed):
    if CHANGELOG_INDEX_NAME_RE.search(path):
        return True
    lines = [t for _, t in added] + list(removed)
    meaningful = [t for t in lines if t.strip()]
    return bool(meaningful) and any(re.search(r"\binclude", t, re.I) for t in meaningful) \
        and all(INDEX_LINE_RE.search(t) for t in meaningful)


def migration_findings(path, added, ctx):
    findings = []
    kinds = Counter()
    rules = COMMON_SQL_RULES + DIALECT_SQL_RULES.get(ctx["dialect"], DIALECT_SQL_RULES["unknown"])
    lb_seen = set()
    changesets = dbms = rollbacks = 0
    added_text = []
    changeset = 0         # index of the changeset the current line belongs to
    guarded = set()       # changesets whose preconditions check the data (sqlCheck, rowCount, tableIsEmpty)
    rb_indent = None      # YAML: indentation of the open "rollback:" key
    in_xml_rollback = False
    prev_no = None
    for line_no, raw in added:
        if prev_no is not None and line_no != prev_no + 1:
            rb_indent, in_xml_rollback = None, False   # not contiguous: a new hunk
        prev_no = line_no
        added_text.append(raw)
        indent = len(raw) - len(raw.lstrip())
        if rb_indent is not None and raw.strip() and indent <= rb_indent:
            rb_indent = None
        m = re.match(r"^(\s*)-?\s*rollback\s*:", raw)
        if m:
            rollbacks += 1
            rb_indent = len(m.group(1))
            continue
        if re.search(r"<\s*rollback\b", raw, re.I):
            rollbacks += 1
            in_xml_rollback = not re.search(r"</\s*rollback\s*>|<\s*rollback[^>]*/>", raw, re.I)
            continue
        if in_xml_rollback:
            if re.search(r"</\s*rollback\s*>", raw, re.I):
                in_xml_rollback = False
            continue
        if re.match(r"^\s*--\s*rollback\b", raw, re.I):
            rollbacks += 1
            continue
        if rb_indent is not None:
            continue     # the rollback of a changeset is not what runs on deploy
        if re.search(r"\bchangeSet\b", raw):
            changeset += 1
        if re.search(r"\b(sqlCheck|rowCount|tableIsEmpty)\b", raw):
            guarded.add(changeset)
        text = strip_sql(raw)
        for sev, kind, rx, detail, unless in rules:
            if rx.search(text) and not (unless and unless.search(text)):
                findings.append(finding(sev, kind, path, line_no, detail))
        for label, rx in SQL_STATEMENTS:
            if rx.search(text):
                kinds[label] += 1
        for name in liquibase_names(raw):
            sev, kind, detail = LIQUIBASE_RULES[name]
            item = finding(sev, kind, path, line_no, detail)
            item["_changeset"] = changeset
            findings.append(item)
            kinds[name] += 1
            lb_seen.add(name)
        if re.search(r"\bchangeSet\b", raw):
            changesets += 1
        if re.search(r"\bdbms\b", raw):
            dbms += 1

    for item in findings:
        if item.pop("_changeset", None) in guarded and item["kind"] == "lb-drop":
            item.update(severity="medium", kind="lb-drop-guarded",
                        detail="drop có preConditions kiểm dữ liệu (sqlCheck/rowCount/tableIsEmpty): "
                               "xác nhận điều kiện và onFail (MARK_RAN giữ lại dữ liệu)")
    joined = "\n".join(added_text)
    if "addColumn" in lb_seen and re.search(r"nullable\s*[:=]\s*[\"']?false", joined, re.I) \
            and not re.search(r"defaultValue", joined):
        findings.append(finding("medium", "lb-add-not-null", path, None,
                                "addColumn với nullable=false không có defaultValue: lỗi nếu bảng đã có dữ liệu"))
    if changesets and not rollbacks and lb_seen & LIQUIBASE_NO_AUTO_ROLLBACK:
        findings.append(finding("info", "lb-no-rollback", path, None,
                                "changeset không tự đảo được ("
                                + ", ".join(sorted(lb_seen & LIQUIBASE_NO_AUTO_ROLLBACK))
                                + ") mà không khai báo rollback"))
    if changesets and 0 < dbms < changesets:
        findings.append(finding("info", "lb-dbms-partial", path, None,
                                f"{dbms}/{changesets} changeset có điều kiện dbms: changeset không có dbms vẫn chạy "
                                "trên DB khác, có thể mất ràng buộc trong im lặng"))
    return findings, dict(kinds)


def config_keys(path, added, removed):
    """Key-level changes in a YAML, properties or .env file, by key name."""
    is_yaml = re.search(r"\.ya?ml$", path, re.I) is not None

    def key_of(text):
        if text.lstrip().startswith("#"):
            return None, None
        if is_yaml:
            m = YAML_KEY_RE.match(text)
            if m:
                return m.group(2), (m.group(4) or "").strip()
        else:
            m = PROPERTIES_KEY_RE.match(text)
            if m:
                return m.group(1), m.group(2).strip()
        return None, None

    added_keys = {}
    for line_no, text in added:
        key, value = key_of(text)
        if key:
            added_keys.setdefault(key, (line_no, value))
    removed_keys = {}
    for text in removed:
        key, value = key_of(text)
        if key:
            removed_keys.setdefault(key, value)

    changes = []
    for key, (line_no, _) in added_keys.items():
        changes.append({"file": path, "change": "sửa" if key in removed_keys else "thêm", "key": key, "line": line_no})
    for key in removed_keys:
        if key not in added_keys:
            changes.append({"file": path, "change": "bỏ", "key": key, "line": None})
    return changes, added_keys


def config_findings(path, changes, added_keys):
    findings = []

    def add(sev, kind, line, detail):
        findings.append(finding(sev, kind, path, line, detail))

    for change in changes:
        key = change["key"]
        k = key.lower()
        line_no = change["line"]
        what = change["change"]
        value = ""
        if key in added_keys:
            value = added_keys[key][1].split(" #")[0].strip().strip("'\"")
        v = value.lower()

        if what == "bỏ":
            add("info", "config-key-removed", None,
                f"`{key}` bị bỏ khỏi config đóng gói: kiểm tra code còn đọc không, môi trường có ghi đè không")
            continue
        if k.endswith("ddl-auto") and v in ("create", "create-drop"):
            add("high", "ddl-auto", line_no, f"`{key}` = {value}: Hibernate sẽ tạo lại schema")
        elif k.endswith("ddl-auto") and v == "update":
            add("medium", "ddl-auto", line_no, f"`{key}` = update: schema bị sửa ngầm, không qua migration")
        if k.endswith("include") and "exposure" in k and "*" in value:
            add("high", "actuator-exposed", line_no, f"`{key}` chứa '*': endpoint actuator lộ ra ngoài")
        if re.search(r"auto[-_.]?offset[-_.]?reset", k) and v == "earliest":
            add("medium", "kafka-offset-earliest", line_no,
                f"`{key}` = earliest: group chưa có offset sẽ đọc lại toàn bộ lịch sử topic")
        if re.search(r"group[-_.]?id$", k):
            add("medium", "kafka-group-changed", line_no,
                f"`{key}` {what}: group mới chưa có offset sẽ đọc theo auto-offset-reset của môi trường")
        if "topic" in k:
            if what == "sửa":
                add("medium", "kafka-topic-changed", line_no,
                    f"`{key}` đổi: topic mới phải tồn tại trên từng cụm; producer/consumer khác và giá trị ghi đè "
                    "trên môi trường phải khớp")
            else:
                add("info", "kafka-topic-added", line_no, f"`{key}` mới: topic phải tồn tại trên từng cụm")
        if "concurrency" in k:
            add("info", "kafka-concurrency", line_no, f"`{key}` {what}: số luồng consumer, kiểm tra số partition")
        if "timeout" in k:
            add("info", "config-timeout", line_no, f"`{key}` {what}: đối chiếu giá trị ghi đè trên từng môi trường")
        if k.endswith("profiles.active") or k.endswith("profiles.include"):
            add("medium", "config-profile", line_no, f"`{key}` {what}: đổi profile đổi toàn bộ cấu hình được nạp")
        if k.endswith("open-in-view") and v == "true":
            add("info", "open-in-view", line_no, "open-in-view = true: lazy load ngoài transaction")
        if SECRET_KEY_RE.search(key) and value and not value.startswith("${") and value not in BLOCK_SCALARS \
                and not value.startswith("ENC("):
            add("medium", "config-secret-value", line_no,
                f"`{key}` có giá trị viết thẳng: kiểm tra có phải secret (giá trị không in ra)")
    return findings


def java_findings(path, added, removed):
    findings = []
    for sev, kind, rx, detail in JAVA_RULES:
        for line_no, text in added:
            if rx.search(text):
                findings.append(finding(sev, kind, path, line_no, detail))
        if kind in JAVA_REMOVED_KINDS:
            for text in removed:
                if rx.search(text):
                    findings.append(finding(sev, kind + "-removed", path, None, "bỏ: " + detail))
    return findings


def dependency_changes(path, added, removed):
    deps = []
    is_pom = path.endswith("pom.xml")
    rx = MAVEN_DEP_RE if is_pom else GRADLE_DEP_RE
    group = 1 if is_pom else 2
    for change, lines in (("thêm", [text for _, text in added]), ("bỏ", removed)):
        for text in lines:
            m = rx.search(text) if is_pom else rx.match(text)
            if m:
                deps.append({"file": path, "change": change, "artifact": m.group(group)})
    return deps


def analyze_content(path, added, removed, ctx):
    """Findings that come from the changed lines alone (no repository lookup)."""
    cats = categorize(path)
    findings = []
    statements = {}
    keys = []
    deps = []
    index = False

    if "migration" in cats:
        index = is_changelog_index(path, added, removed)
        if not index:
            mig, statements = migration_findings(path, added, ctx)
            findings.extend(mig)

    if CONFIG_TEXT_RE.search(path) and ("config" in cats or "deploy" in cats):
        keys, added_keys = config_keys(path, added, removed)
        findings.extend(config_findings(path, keys, added_keys))

    if path.endswith(".java") and "test" not in cats:
        findings.extend(java_findings(path, added, removed))

    if "build" in cats:
        deps = dependency_changes(path, added, removed)
        if deps:
            findings.append(finding("info", "dependency-changed", path, None,
                                    f"{len(deps)} dòng dependency đổi: kiểm tra version và xung đột"))

    return {"categories": cats, "findings": findings, "statements": statements,
            "config_keys": keys, "dependencies": deps, "changelog_index": index}


# ---------------------------------------------------------------- range-level migration checks

def flyway_version(path):
    m = FLYWAY_RE.search(path)
    if not m:
        return None
    return tuple(int(x) for x in re.split(r"[._]", m.group("version")))


def flyway_findings(repo, base_sha, head_sha, files):
    findings = []
    base_versions = {}
    for p in run_git(repo, ["ls-tree", "-r", "--name-only", base_sha], check=False).splitlines():
        v = flyway_version(p)
        if v is not None:
            base_versions.setdefault(v, p)
    head_versions = Counter()
    for p in run_git(repo, ["ls-tree", "-r", "--name-only", head_sha], check=False).splitlines():
        v = flyway_version(p)
        if v is not None:
            head_versions[v] += 1
    top = max(base_versions) if base_versions else None
    for f in files:
        if f["status"] != "A":
            continue
        v = flyway_version(f["path"])
        if v is None:
            continue
        label = ".".join(str(x) for x in v)
        if head_versions[v] > 1:
            findings.append(finding("high", "flyway-duplicate-version", f["path"], None,
                                    f"phiên bản Flyway V{label} bị trùng: Flyway dừng khi khởi động"))
        elif top is not None and v < top:
            findings.append(finding("medium", "flyway-out-of-order", f["path"], None,
                                    f"V{label} nhỏ hơn bản lớn nhất trên nhánh đích "
                                    f"(V{'.'.join(str(x) for x in top)}): Flyway bỏ qua hoặc từ chối nếu không bật "
                                    "outOfOrder"))
    return findings


def include_paths(text):
    return [m.group(1) for m in INCLUDE_PATH_RE.finditer(text)]


def leading_number(path):
    m = re.match(r"(\d+)", os.path.basename(path))
    return int(m.group(1)) if m else None


def changelog_index_findings(repo, head_sha, f, patch):
    """Checks on a Liquibase master/index changelog that the range modified."""
    path = f["path"]
    findings = []
    added = [t for _, t in patch["added"]]
    removed = [t for t in patch["removed"] if t.strip()]
    added_norm = {t.strip() for t in added if t.strip()}
    removed_inc = [t for t in removed if re.search(r"\binclude|\bfile[\"']?\s*[:=]", t, re.I)]
    if f["status"] in ("M", "D") and removed_inc:
        if any(t.strip() not in added_norm for t in removed_inc):
            findings.append(finding("high", "lb-include-changed", path, None,
                                    "include đã có bị bỏ hoặc đổi đường dẫn: Liquibase nhận diện changeset theo "
                                    "đường dẫn file, đổi đường dẫn làm changeset chạy lại hoặc bị bỏ sót"))
        else:
            findings.append(finding("medium", "lb-include-moved", path, None,
                                    "include đã có bị đổi thứ tự: thứ tự chạy đổi với môi trường mới"))

    new_paths = set(include_paths("\n".join(added)))
    if new_paths and f["status"] != "D":
        ordered = include_paths(run_git(repo, ["show", f"{head_sha}:{path}"], check=False))
        highest = None
        for p in ordered:
            n = leading_number(p)
            if p in new_paths and n is not None and highest is not None and n < highest[0]:
                findings.append(finding("info", "lb-include-order", path, None,
                                        f"`{os.path.basename(p)}` khai báo sau `{os.path.basename(highest[1])}`: "
                                        "Liquibase chạy theo thứ tự khai báo, không theo số; dễ nhầm khi thêm file sau"))
            if n is not None and (highest is None or n > highest[0]):
                highest = (n, p)
    return findings


def not_included_findings(repo, head_sha, files):
    """New Liquibase change files that no changelog includes (skipped when a changelog uses includeAll)."""
    candidates = [f for f in files if f["status"] == "A" and "migration" in f["categories"]
                  and not f.get("changelog_index") and flyway_version(f["path"]) is None
                  and not FLYWAY_OTHER_RE.search(f["path"])]
    if not candidates:
        return []
    out = run_git(repo, ["grep", "-l", "-E", r"include(All)?\b", head_sha, "--",
                         "*.yaml", "*.yml", "*.xml", "*.json"], check=False)
    prefix = f"{head_sha}:"
    includers = [line[len(prefix):] if line.startswith(prefix) else line for line in out.splitlines()]
    includers = [p for p in includers if "migration" in categorize(p)]
    if not includers:
        return []
    texts = {p: run_git(repo, ["show", f"{head_sha}:{p}"], check=False) for p in includers}
    if any(re.search(r"\bincludeAll\b", t) for t in texts.values()):
        return []
    findings = []
    for f in candidates:
        name = os.path.basename(f["path"])
        if not any(name in t for p, t in texts.items() if p != f["path"]):
            findings.append(finding("medium", "lb-not-included", f["path"], None,
                                    "file migration mới không được include trong changelog nào: sẽ không chạy"))
    return findings


# ---------------------------------------------------------------- merge check, names

def git_version(repo):
    out = run_git(repo, ["version"], check=False)
    m = re.search(r"(\d+)\.(\d+)", out)
    return (int(m.group(1)), int(m.group(2))) if m else (0, 0)


def merge_check(repo, base_sha, head_sha):
    if git_version(repo) < (2, 38):
        return {"status": "unknown", "conflicts": [], "reason": "git < 2.38 không có merge-tree --write-tree"}
    proc = subprocess.run(
        ["git", "-C", repo, "merge-tree", "--write-tree", "--name-only", "--no-messages", base_sha, head_sha],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if proc.returncode == 0:
        return {"status": "clean", "conflicts": []}
    if proc.returncode == 1:
        names = []
        for line in proc.stdout.splitlines()[1:]:
            if line.strip() and line not in names:
                names.append(line)
        return {"status": "conflict", "conflicts": names}
    first = (proc.stderr or "").strip().splitlines()[:1]
    return {"status": "unknown", "conflicts": [], "reason": first[0] if first else f"exit {proc.returncode}"}


def slug(ref):
    ref = re.sub(r"^(refs/heads/|refs/remotes/)", "", ref)
    ref = re.sub(r"^origin/", "", ref)
    return re.sub(r"[^\w.-]+", "-", ref).strip("-.") or "head"


def report_name(repo, base, head, pr, today):
    if pr:
        top = run_git(repo, ["rev-parse", "--show-toplevel"], check=False).strip()
        return f"review-pr-{pr}-{slug(os.path.basename(top) or 'repo')}-{today}.md"
    return f"review-{slug(head)}-to-{slug(base)}-{today}.md"


# ---------------------------------------------------------------- scan

def parse_commits(text):
    rows = []
    for record in text.split("\x1e"):
        record = record.strip("\n")
        if not record.strip():
            continue
        parts = record.split("\x1f")
        if len(parts) < 5:
            continue
        body = redact(parts[5].strip() if len(parts) > 5 else "")
        rows.append({"sha": parts[0], "short": parts[1], "author": parts[2], "date": parts[3],
                     "subject": parts[4].strip(), "body": body[:400]})
    return rows


SCRUB_RE = re.compile(r"(?i)\b(password|passwd|secret|token|api[-_]?key)\b(\s*[:=]\s*)\S+")


def redact(text):
    return SCRUB_RE.sub(r"\1\2<redacted>", text)


def collapse(findings):
    """One finding per (severity, kind, file, detail), with every line it was seen on."""
    groups = {}
    for f in findings:
        key = (f["severity"], f["kind"], f["file"], f["detail"])
        g = groups.get(key)
        if g is None:
            g = groups[key] = {**f, "lines": [], "count": 0}
        g["count"] += 1
        if f["line"] is not None and f["line"] not in g["lines"]:
            g["lines"].append(f["line"])
    out = []
    for g in groups.values():
        g["lines"].sort()
        g["line"] = g["lines"][0] if g["lines"] else None
        out.append(g)
    return out


def where_of(f):
    lines = f.get("lines") or ([f["line"]] if f.get("line") else [])
    if lines:
        shown = ",".join(str(n) for n in lines[:8]) + ("…" if len(lines) > 8 else "")
        return f"{f['file']}:{shown}"
    return f["file"] + (f" (x{f['count']})" if f.get("count", 1) > 1 else "")


def resolve_dialect(repo, head_sha, flag):
    if flag and flag != "auto":
        return flag, "flag"
    configured, _ = setting("SPRING_PR_REVIEW_DIALECT", "auto")
    if configured and configured.lower() in DIALECTS:
        return configured.lower(), "setting"
    return detect_dialect(repo, head_sha), "detected"


def scan(repo, base, head, dialect_flag="auto", pr=None, merge=True, today=None):
    base_sha = resolve(repo, base)
    head_sha = resolve(repo, head)
    merge_base = run_git(repo, ["merge-base", base_sha, head_sha], check=False).strip()
    if not merge_base:
        raise ScanError("hai ref không có commit chung")
    dialect, dialect_source = resolve_dialect(repo, head_sha, dialect_flag)
    ctx = {"dialect": dialect}

    commits = parse_commits(run_git(
        repo, ["log", "--no-merges", "--reverse", "--date=short", f"--format={COMMIT_FORMAT}",
               f"{base_sha}..{head_sha}"]
    ))
    merges = run_git(repo, ["rev-list", "--merges", "--count", f"{base_sha}..{head_sha}"], check=False).strip()
    name_status = parse_name_status(run_git(repo, ["diff", "--no-renames", "--name-status", merge_base, head_sha]))
    range_patch = {f["path"]: f for f in parse_patch(
        run_git(repo, ["diff", "--no-renames", "-U0", merge_base, head_sha]))}

    files = []
    findings = []
    config_changes = []
    statements_by_file = {}
    dependencies = []
    for path, f in range_patch.items():
        status = name_status.get(path, "M")
        result = analyze_content(path, f["added"], f["removed"], ctx)
        files.append({"path": path, "status": status, "categories": result["categories"],
                      "changelog_index": result["changelog_index"],
                      "added": len(f["added"]), "removed": len(f["removed"])})
        findings.extend(result["findings"])
        config_changes.extend(result["config_keys"])
        dependencies.extend(result["dependencies"])
        if result["statements"]:
            statements_by_file[path] = result["statements"]

    # File-level checks that depend on the target branch.
    for f in files:
        if "migration" not in f["categories"]:
            continue
        if f["changelog_index"]:
            findings.extend(changelog_index_findings(repo, head_sha, f, range_patch[f["path"]]))
        elif f["status"] in ("M", "D"):
            findings.append(finding("high", "migration-existing-changed", f["path"], None,
                                    "migration đã có trên nhánh đích bị sửa hoặc xóa: đổi checksum, môi trường "
                                    "đã chạy sẽ lỗi khi khởi động; tạo migration mới thay vì sửa"))
    findings.extend(flyway_findings(repo, base_sha, head_sha, files))
    findings.extend(not_included_findings(repo, head_sha, files))

    kinds = {x["kind"] for x in findings}
    if "kafka-offset-earliest" in kinds and kinds & {"kafka-group-changed", "kafka-listener"}:
        findings.append(finding("high", "kafka-replay-risk", "-", None,
                                "consumer/group mới cùng auto-offset-reset=earliest: lần chạy đầu đọc lại toàn bộ "
                                "lịch sử topic còn trong retention; chốt offset trước khi deploy"))

    findings.sort(key=lambda x: (SEVERITY_ORDER[x["severity"]], x["file"], x["line"] or 0))
    findings = collapse(findings)

    file_level = {}
    for x in findings:
        if x["kind"] in FILE_LEVEL_KINDS:
            file_level.setdefault(x["file"], set()).add(x["kind"])

    commit_rows = []
    for c in commits:
        patch_files = parse_patch(run_git(repo, ["show", "--format=", "--no-renames", "-U0", c["sha"]]))
        kinds_c = set()
        cats = set()
        paths = []
        added_total = removed_total = 0
        for pf in patch_files:
            res = analyze_content(pf["path"], pf["added"], pf["removed"], ctx)
            cats.update(res["categories"])
            kinds_c.update(x["kind"] for x in res["findings"])
            kinds_c.update(file_level.get(pf["path"], ()))
            paths.append(pf["path"])
            added_total += len(pf["added"])
            removed_total += len(pf["removed"])
        commit_rows.append({**c, "files": paths, "categories": sorted(cats), "flags": sorted(kinds_c),
                            "added": added_total, "removed": removed_total})

    migrations = [
        {"file": f["path"], "status": f["status"], "changelog_index": f["changelog_index"],
         "statements": statements_by_file.get(f["path"], {})}
        for f in files if "migration" in f["categories"]
    ]
    category_counts = Counter(c for f in files for c in f["categories"])
    today = today or datetime.date.today().strftime("%Y%m%d")

    return {
        "range": {
            "base": base,
            "base_sha": base_sha,
            "head": head,
            "head_sha": head_sha,
            "merge_base": merge_base,
            "current_branch": run_git(repo, ["rev-parse", "--abbrev-ref", "HEAD"], check=False).strip(),
            "commits": len(commit_rows),
            "merge_commits_skipped": int(merges or 0),
            "files": len(files),
            "added": sum(f["added"] for f in files),
            "removed": sum(f["removed"] for f in files),
            "authors": sorted({c["author"] for c in commit_rows}),
            "first_date": commit_rows[0]["date"] if commit_rows else None,
            "last_date": commit_rows[-1]["date"] if commit_rows else None,
            "dialect": dialect,
            "dialect_source": dialect_source,
            "touches_build": category_counts.get("build", 0) > 0,
            "categories": dict(category_counts),
            "merge": merge_check(repo, base_sha, head_sha) if merge else {"status": "skipped", "conflicts": []},
            "report_name": report_name(repo, base, head, pr, today),
        },
        "commits": commit_rows,
        "files": files,
        "findings": findings,
        "config_changes": config_changes,
        "migrations": migrations,
        "dependencies": dependencies,
    }


def render_text(report):
    r = report["range"]
    merge = r["merge"]
    merge_line = merge["status"] + (f": {', '.join(merge['conflicts'])}" if merge.get("conflicts") else "") \
        + (f" ({merge['reason']})" if merge.get("reason") else "")
    lines = [
        f"Range: {r['base']} ({r['base_sha'][:7]}) -> {r['head']} ({r['head_sha'][:7]}), merge-base {r['merge_base'][:7]}",
        f"Commits: {r['commits']} (merge commits skipped: {r['merge_commits_skipped']})  "
        f"Files: {r['files']}  +{r['added']} -{r['removed']}",
        f"Authors: {', '.join(r['authors']) or '-'} ({r['first_date'] or '-'} .. {r['last_date'] or '-'})",
        f"Merge: {merge_line}   Build files touched: {'yes' if r['touches_build'] else 'no'}",
        f"Database: {r['dialect']} ({r['dialect_source']})   Report: {r['report_name']}",
        "",
        "Commits:",
    ]
    for c in report["commits"]:
        flags = f"  flags: {', '.join(c['flags'])}" if c["flags"] else ""
        lines.append(f"  {c['short']} {c['date']} {c['subject']}  [{', '.join(c['categories'])}]{flags}")
    lines.append("")
    lines.append(f"Findings ({len(report['findings'])}):")
    for x in report["findings"]:
        lines.append(f"  [{x['severity']}] {x['kind']} {where_of(x)} - {x['detail']}")
    if report["config_changes"]:
        lines.append("")
        lines.append("Config key changes (by key name):")
        for c in report["config_changes"]:
            lines.append(f"  {c['change']} {c['key']}  ({c['file']})")
    if report["migrations"]:
        lines.append("")
        lines.append("Migrations:")
        for m in report["migrations"]:
            stm = ", ".join(f"{k}:{v}" for k, v in m["statements"].items()) or "-"
            tag = "  (changelog index)" if m["changelog_index"] else ""
            lines.append(f"  {m['status']} {m['file']}  [{stm}]{tag}")
    return "\n".join(lines) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description="Scan a git range for a Spring Boot PR review.")
    parser.add_argument("--base", required=True, help="target branch or commit (e.g. origin/staging)")
    parser.add_argument("--head", required=True, help="source branch or commit (e.g. origin/feature/x)")
    parser.add_argument("--repo", default=".", help="repository directory (default: current)")
    parser.add_argument("--format", choices=["json", "text"], default="json")
    parser.add_argument("--dialect", choices=("auto",) + DIALECTS, default="auto",
                        help="database for the SQL rules; auto reads JDBC URLs and drivers in the repository")
    parser.add_argument("--pr", help="pull-request number, used in the suggested report name")
    parser.add_argument("--no-merge-check", action="store_true", help="skip the trial merge (git merge-tree)")
    parser.add_argument("--patch-file", help="also write the full diff merge-base..head (-U5) to this file")
    args = parser.parse_args(argv)

    try:
        report = scan(args.repo, args.base, args.head, args.dialect, args.pr, not args.no_merge_check)
        if args.patch_file:
            r = report["range"]
            patch = run_git(args.repo, ["diff", "--no-renames", "-U5", r["merge_base"], r["head_sha"]])
            folder = os.path.dirname(os.path.abspath(args.patch_file))
            os.makedirs(folder, exist_ok=True)
            with open(args.patch_file, "w", encoding="utf-8") as fh:
                fh.write(patch)
    except ScanError as exc:
        print(f"pr_scan: {exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"pr_scan: không ghi được {args.patch_file}: {exc.strerror or exc}", file=sys.stderr)
        return 2

    if args.format == "text":
        sys.stdout.write(render_text(report))
    else:
        json.dump(report, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
