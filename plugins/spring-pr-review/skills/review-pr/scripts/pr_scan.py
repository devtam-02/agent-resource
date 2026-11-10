#!/usr/bin/env python3
"""Scan a git range for a Spring Boot pull-request review: commits, file categories, risk findings.

Read-only: runs only git rev-parse, merge-base, log, rev-list, diff, diff-tree and show. Never checks
out, fetches, merges or writes into the repository.

Secret values are never printed. A secret-looking key is reported by its name and line number only;
SQL statements are reported by kind only. Read the file itself when the context matters.

Examples:
  python3 pr_scan.py --base origin/staging --head origin/feature/x
  python3 pr_scan.py --repo ~/work/service --base staging --head HEAD --format text
  python3 pr_scan.py --base staging --head HEAD --patch-file "$SCRATCH/pr.patch"

Exit codes: 0 ok, 2 usage or git error.
"""

import argparse
import json
import re
import subprocess
import sys
from collections import Counter

SEVERITY_ORDER = {"high": 0, "medium": 1, "info": 2}
COMMIT_FORMAT = "%H%x1f%h%x1f%an%x1f%ad%x1f%s%x1f%b%x1e"

MIGRATION_DIR_RE = re.compile(
    r"(^|/)(db/migrations?|db/changelogs?|migrations?|liquibase|flyway)(/|$)", re.I
)
MIGRATION_NAME_RE = re.compile(r"(^|/)V[\w.]*__[^/]*\.sql$", re.I)
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
SECURITY_RE = re.compile(r"security|jwt|oauth|keycloak|auth(?!or)|filter", re.I)
API_RE = re.compile(r"controller|resource|endpoint", re.I)
PERSIST_RE = re.compile(
    r"(^|/)(entity|entities|domain|model|persistence|repository)(/|$)|Repository\.java$|Entity\.java$",
    re.I,
)
CONFIG_TEXT_RE = re.compile(r"\.(ya?ml|properties)$|(^|/)\.env(\.[\w-]+)?$", re.I)

# SQL rules run on added lines of migration files. Output names the rule, never the statement text.
SQL_RULES = [
    ("high", "sql-drop", re.compile(r"\bdrop\s+(table|column|index|constraint|schema|view)\b", re.I),
     "DROP: mất dữ liệu hoặc phụ thuộc, kiểm tra còn code đọc không"),
    ("high", "sql-truncate", re.compile(r"\btruncate\b", re.I), "TRUNCATE: xóa toàn bộ dữ liệu bảng"),
    ("high", "sql-rename", re.compile(r"\brename\s+(to|column)\b", re.I),
     "RENAME: code cũ và đang chạy sẽ lỗi trong lúc deploy"),
    ("high", "sql-type-change", re.compile(r"\bset\s+data\s+type\b|\balter\s+column\b.*\btype\b", re.I),
     "đổi kiểu cột: có thể mất dữ liệu hoặc khóa bảng"),
    ("medium", "sql-not-null", re.compile(r"\bset\s+not\s+null\b", re.I),
     "NOT NULL trên cột đã có: lỗi nếu còn dòng NULL"),
    ("medium", "sql-add-not-null", re.compile(r"\badd\s+(column\s+)?\S+\s+[^,;]*\bnot\s+null\b(?![^,;]*\bdefault\b)", re.I),
     "cột NOT NULL mới không có DEFAULT: lỗi nếu bảng đã có dữ liệu"),
    ("medium", "sql-delete-no-where", re.compile(r"\bdelete\s+from\b(?![^;]*\bwhere\b)", re.I),
     "DELETE không thấy WHERE trên dòng này: kiểm tra có nhiều dòng"),
    ("medium", "sql-update-no-where", re.compile(r"\bupdate\s+\S+\s+set\b(?![^;]*\bwhere\b)", re.I),
     "UPDATE không thấy WHERE trên dòng này: kiểm tra có nhiều dòng"),
    ("medium", "sql-index-lock", re.compile(r"\bcreate\s+(unique\s+)?index\b(?![^;]*\bconcurrently\b)", re.I),
     "CREATE INDEX không CONCURRENTLY: có thể khóa bảng lớn lúc chạy (PostgreSQL)"),
]
SQL_STATEMENTS = [
    ("create table", re.compile(r"\bcreate\s+table\b", re.I)),
    ("alter table", re.compile(r"\balter\s+table\b", re.I)),
    ("create index", re.compile(r"\bcreate\s+(unique\s+)?index\b", re.I)),
    ("insert", re.compile(r"\binsert\s+into\b", re.I)),
    ("update", re.compile(r"\bupdate\s+\S+\s+set\b", re.I)),
    ("delete", re.compile(r"\bdelete\s+from\b", re.I)),
    ("drop", re.compile(r"\bdrop\s+", re.I)),
]

# Keys are matched by name only, not by full path (YAML nesting is not tracked on a diff).
YAML_KEY_RE = re.compile(r"^(\s*)-?\s*([A-Za-z0-9_.\-\[\]]+)\s*:(\s+(.*)|\s*)$")
PROPERTIES_KEY_RE = re.compile(r"^\s*([A-Za-z0-9_.\-\[\]]+)\s*[=:]\s*(.*)$")
SECRET_KEY_RE = re.compile(r"(?i)password|passwd|secret|token|api[-_.]?key|private[-_.]?key|credential")
BLOCK_SCALARS = {"|", ">", "|-", ">-", "|+", ">+"}

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
    ("medium", "security-open", re.compile(r"permitAll\(\)|csrf\(\)\s*\.\s*disable\(\)"),
     "mở quyền truy cập hoặc tắt CSRF: kiểm tra có chủ đích"),
    ("info", "async-entry",
     re.compile(r"@(Scheduled|Async|KafkaListener|RabbitListener|JmsListener|EventListener|TransactionalEventListener)\b"),
     "điểm vào chạy nền hoặc bất đồng bộ"),
    ("info", "native-query", re.compile(r"nativeQuery\s*=\s*true"),
     "native query: kiểm tra SQL, index và DB đích"),
    ("info", "client-no-timeout", re.compile(r"new\s+RestTemplate\s*\(\s*\)|WebClient\.create\s*\(\s*\)"),
     "HTTP client không cấu hình timeout tường minh"),
    ("info", "broad-catch", re.compile(r"catch\s*\(\s*(final\s+)?(Exception|Throwable)\b"),
     "catch rộng"),
]
JAVA_REMOVED_KINDS = {"jpa-mapping", "transactional", "api-mapping", "async-entry"}

MAVEN_DEP_RE = re.compile(r"<artifactId>([^<]+)</artifactId>")
GRADLE_DEP_RE = re.compile(
    r"^\s*(implementation|api|compileOnly|runtimeOnly|testImplementation|annotationProcessor)\b\s*\(?\s*[\"']([^\"']+)"
)
HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")


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


def categorize(path):
    cats = []
    if MIGRATION_DIR_RE.search(path) or MIGRATION_NAME_RE.search(path):
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


def config_keys(path, added, removed):
    """Key-level changes in a YAML or properties file, by key name."""
    is_yaml = re.search(r"\.ya?ml$", path, re.I) is not None

    def key_of(text):
        if is_yaml:
            m = YAML_KEY_RE.match(text)
            if m:
                value = (m.group(4) or "").strip()
                return m.group(2), value
        else:
            m = PROPERTIES_KEY_RE.match(text)
            if m and not text.lstrip().startswith("#"):
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
        change = "sửa" if key in removed_keys else "thêm"
        changes.append({"file": path, "change": change, "key": key, "line": line_no})
    for key in removed_keys:
        if key not in added_keys:
            changes.append({"file": path, "change": "bỏ", "key": key, "line": None})
    return changes, added_keys


def config_findings(path, added_keys, added):
    findings = []

    def add(sev, kind, line, detail):
        findings.append({"severity": sev, "kind": kind, "file": path, "line": line, "detail": detail})

    for key, (line_no, value) in added_keys.items():
        value_clean = value.strip().strip("'\"")
        if key.lower().endswith("ddl-auto") and value_clean:
            if value_clean.lower() in ("create", "create-drop"):
                add("high", "ddl-auto", line_no, f"ddl-auto = {value_clean}: Hibernate sẽ tạo lại schema")
            elif value_clean.lower() == "update":
                add("medium", "ddl-auto", line_no, "ddl-auto = update: schema bị sửa ngầm, không qua migration")
        if key.lower().endswith("include") and "*" in value_clean:
            add("high", "actuator-exposed", line_no, "endpoint actuator lộ ra ngoài (include chứa '*')")
        if SECRET_KEY_RE.search(key) and value_clean and not value_clean.startswith("${") \
                and value_clean not in BLOCK_SCALARS and not value_clean.startswith("#"):
            add("medium", "config-secret-value", line_no,
                "key nhạy cảm có giá trị viết thẳng: kiểm tra có phải secret, giá trị không in ra")
        if key.lower() == "open-in-view" and value_clean.lower() == "true":
            add("info", "open-in-view", line_no, "open-in-view = true: lazy load ngoài transaction")
    return findings


def sql_findings(path, added):
    findings = []
    kinds = Counter()
    for line_no, text in added:
        for sev, kind, rx, detail in SQL_RULES:
            if rx.search(text):
                findings.append({"severity": sev, "kind": kind, "file": path, "line": line_no, "detail": detail})
        for label, rx in SQL_STATEMENTS:
            if rx.search(text):
                kinds[label] += 1
    return findings, dict(kinds)


def java_findings(path, added, removed):
    findings = []
    for sev, kind, rx, detail in JAVA_RULES:
        for line_no, text in added:
            if rx.search(text):
                findings.append({"severity": sev, "kind": kind, "file": path, "line": line_no, "detail": detail})
        if kind in JAVA_REMOVED_KINDS:
            for text in removed:
                if rx.search(text):
                    findings.append({"severity": sev, "kind": kind + "-removed", "file": path, "line": None,
                                     "detail": "bỏ: " + detail})
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


def analyze_file(path, status, added, removed):
    cats = categorize(path)
    findings = []
    statements = {}
    keys = []
    deps = []

    if "migration" in cats:
        if status in ("M", "D", "R", "T"):
            findings.append({"severity": "high", "kind": "migration-existing-changed", "file": path, "line": None,
                             "detail": "migration đã có trên nhánh đích bị sửa hoặc xóa: đổi checksum, hỏng môi trường đã chạy"})
        sql, statements = sql_findings(path, added)
        findings.extend(sql)

    if CONFIG_TEXT_RE.search(path) and ("config" in cats or "deploy" in cats):
        keys, added_keys = config_keys(path, added, removed)
        findings.extend(config_findings(path, added_keys, added))

    if path.endswith(".java"):
        findings.extend(java_findings(path, added, removed))

    if "build" in cats:
        deps = dependency_changes(path, added, removed)
        if deps:
            findings.append({"severity": "info", "kind": "dependency-changed", "file": path, "line": None,
                             "detail": f"{len(deps)} dòng dependency đổi: kiểm tra version và xung đột"})

    return {
        "categories": cats,
        "findings": findings,
        "statements": statements,
        "config_keys": keys,
        "dependencies": deps,
    }


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
        rows.append({
            "sha": parts[0],
            "short": parts[1],
            "author": parts[2],
            "date": parts[3],
            "subject": parts[4].strip(),
            "body": body[:400],
        })
    return rows


SCRUB_RE = re.compile(r"(?i)\b(password|passwd|secret|token|api[-_]?key)\b(\s*[:=]\s*)\S+")


def redact(text):
    return SCRUB_RE.sub(r"\1\2<redacted>", text)


def scan(repo, base, head):
    base_sha = resolve(repo, base)
    head_sha = resolve(repo, head)
    merge_base = run_git(repo, ["merge-base", base_sha, head_sha], check=False).strip()
    if not merge_base:
        raise ScanError("hai ref không có commit chung")

    commits = parse_commits(run_git(
        repo, ["log", "--no-merges", "--reverse", f"--format={COMMIT_FORMAT}", f"{base_sha}..{head_sha}"]
    ))
    merges = run_git(repo, ["rev-list", "--merges", "--count", f"{base_sha}..{head_sha}"], check=False).strip()
    name_status = parse_name_status(run_git(
        repo, ["diff", "--no-renames", "--name-status", merge_base, head_sha]
    ))
    range_files = parse_patch(run_git(repo, ["diff", "--no-renames", "-U0", merge_base, head_sha]))

    files = []
    findings = []
    config_changes = []
    statements_by_file = {}
    dependencies = []
    for f in range_files:
        status = name_status.get(f["path"], "M")
        result = analyze_file(f["path"], status, f["added"], f["removed"])
        files.append({
            "path": f["path"],
            "status": status,
            "categories": result["categories"],
            "added": len(f["added"]),
            "removed": len(f["removed"]),
        })
        findings.extend(result["findings"])
        config_changes.extend(result["config_keys"])
        dependencies.extend(result["dependencies"])
        if result["statements"]:
            statements_by_file[f["path"]] = result["statements"]

    findings.sort(key=lambda x: (SEVERITY_ORDER[x["severity"]], x["file"], x["line"] or 0))

    migrations = [
        {"file": f["path"], "status": f["status"], "statements": statements_by_file.get(f["path"], {})}
        for f in files if "migration" in f["categories"]
    ]

    commit_rows = []
    for c in commits:
        patch_files = parse_patch(run_git(repo, ["show", "--format=", "--no-renames", "-U0", c["sha"]]))
        c_status = parse_name_status(run_git(repo, ["diff-tree", "-r", "--no-commit-id", "--no-renames",
                                                    "--name-status", c["sha"]], check=False))
        kinds = set()
        cats = set()
        file_paths = []
        added_total = 0
        removed_total = 0
        for pf in patch_files:
            res = analyze_file(pf["path"], c_status.get(pf["path"], "M"), pf["added"], pf["removed"])
            cats.update(res["categories"])
            kinds.update(x["kind"] for x in res["findings"])
            file_paths.append(pf["path"])
            added_total += len(pf["added"])
            removed_total += len(pf["removed"])
        commit_rows.append({
            **c,
            "files": file_paths,
            "categories": sorted(cats),
            "flags": sorted(kinds),
            "added": added_total,
            "removed": removed_total,
        })

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
    lines = [
        f"Range: {r['base']} ({r['base_sha'][:7]}) -> {r['head']} ({r['head_sha'][:7]}), merge-base {r['merge_base'][:7]}",
        f"Commits: {r['commits']} (merge commits skipped: {r['merge_commits_skipped']})  "
        f"Files: {r['files']}  +{r['added']} -{r['removed']}",
        "",
        "Commits:",
    ]
    for c in report["commits"]:
        flags = f"  flags: {', '.join(c['flags'])}" if c["flags"] else ""
        lines.append(f"  {c['short']} {c['subject']}  [{', '.join(c['categories'])}]{flags}")
    lines.append("")
    lines.append(f"Findings ({len(report['findings'])}):")
    for x in report["findings"]:
        where = f"{x['file']}:{x['line']}" if x["line"] else x["file"]
        lines.append(f"  [{x['severity']}] {x['kind']} {where} - {x['detail']}")
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
            lines.append(f"  {m['status']} {m['file']}  [{stm}]")
    return "\n".join(lines) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description="Scan a git range for a Spring Boot PR review (read-only).")
    parser.add_argument("--base", required=True, help="target branch or commit (e.g. origin/staging)")
    parser.add_argument("--head", required=True, help="source branch or commit (e.g. HEAD)")
    parser.add_argument("--repo", default=".", help="repository directory (default: current)")
    parser.add_argument("--format", choices=["json", "text"], default="json")
    parser.add_argument("--patch-file", help="also write the full diff merge-base..head (-U5) to this file")
    args = parser.parse_args(argv)

    try:
        report = scan(args.repo, args.base, args.head)
        if args.patch_file:
            r = report["range"]
            patch = run_git(args.repo, ["diff", "--no-renames", "-U5", r["merge_base"], r["head_sha"]])
            with open(args.patch_file, "w", encoding="utf-8") as fh:
                fh.write(patch)
    except ScanError as exc:
        print(f"pr_scan: {exc}", file=sys.stderr)
        return 2

    if args.format == "text":
        sys.stdout.write(render_text(report))
    else:
        json.dump(report, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
