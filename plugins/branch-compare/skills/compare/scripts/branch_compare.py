#!/usr/bin/env python3
"""Compare two branches before merging the source into the target (default: staging into release).

Commits: what the target is missing, with the ones already there split out (same patch, cherry-picked
with -x, or a target commit with the same subject), reverts inside the batch, commits whose files the
merge would not change, and the commits only the target has (hotfixes to merge back).

Impact: the diff from the target to a trial merge result (git merge-tree --write-tree), so commits
already cherry-picked add nothing and hotfixes only on the target are kept. The diff is scanned with
spring-pr-review's rules (pr_scan.py, copied unchanged) plus checks for a release: config keys the code
needs without a default, environment-specific config files, staging-looking values in shared config,
Kafka topics declared in code and message classes, SNAPSHOT dependencies and version properties,
migrations only on the target, tickets that are only partly on the target, sensitive areas (money,
permissions, scheduled jobs, outgoing messages, deletes).

Read-only on branches, the index and the working tree. The trial merge stores its objects in
.git/objects, unreferenced (git gc removes them); --no-merge-check skips it and falls back to the diff
merge-base..source. Secret values are never printed.

  branch_compare.py                                    # origin/staging into origin/release
  branch_compare.py --source staging --target release --out "$TMP/compare.json" --format text
  branch_compare.py --target master --patch-file "$TMP/merge.patch"

A bare branch name resolves to origin/<name> when that exists, else to the local branch.
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
import pr_scan as ps  # noqa: E402  (copy of spring-pr-review's; tests/test_vendored.py keeps them equal)
from env_overrides import canonical  # noqa: E402

LOG_FORMAT = "%m%x1f%H%x1f%h%x1f%an%x1f%ad%x1f%s%x1f%b%x1e"
TICKET_RE = re.compile(r"\b([A-Z][A-Z0-9]{1,9}-\d{1,6})\b")
NOT_TICKETS = {"UTF", "ISO", "SHA", "TLS", "SSL", "HTTP", "HTTPS", "RFC", "JDK", "JSR", "CVE", "AES", "RSA",
               "UUID", "GMT", "UTC", "MD"}
CHERRY_RE = re.compile(r"\(cherry picked from commit ([0-9a-f]{7,40})\)")
REVERT_SUBJECT_RE = re.compile(r'^Revert\s+"(.+)"$')
REVERT_BODY_RE = re.compile(r"This reverts commit ([0-9a-f]{7,40})")

# ---------------------------------------------------------------- environments

DEFAULT_TARGET_ENVS = ("uat", "prod", "production", "release", "live", "preprod", "pilot")
SOURCE_ENVS = frozenset({"staging", "stg", "stag", "dev", "develop", "development", "local", "sit", "qa", "test"})
ENV_NAME_RE = re.compile(
    r"^(?:application|bootstrap|values|config|docker-compose|settings)[-_.]([a-z0-9][a-z0-9-]*)"
    r"\.(?:ya?ml|properties|json|env)$"
)
DOTENV_RE = re.compile(r"^\.env\.([\w-]+)$")
PACKAGED_RE = re.compile(r"(^|/)src/main/resources/(application|bootstrap)([-.][\w-]+)?\.(ya?ml|properties)$", re.I)

# ---------------------------------------------------------------- release checks

PLACEHOLDER_RE = re.compile(r"(?<!:)\$\{([^${}:]+)\}")          # ${name} without a default
VALUE_ANNOTATION_RE = re.compile(r'@Value\(\s*"[^"]*?\$\{([^${}:"]+)\}')
STAGING_VALUE_RE = re.compile(r"(?i)\b(localhost|staging|stg|dev|mock|sandbox)\b|\b127\.0\.0\.1\b")
TOPIC_DECL_RE = re.compile(r"\bnew\s+NewTopic\s*\(|\bTopicBuilder\s*\.\s*name\s*\(")
MESSAGE_PATH_RE = re.compile(r"(^|/)(events?|messages?|kafka|payloads?|avro)/[^/]+\.java$|(Event|Message|Payload)\.java$")
NOT_MESSAGE_RE = re.compile(r"(Publisher|Producer|Consumer|Listener|Handler|Service|Config|Configuration|Mapper"
                            r"|Adapter|Factory|Utils?|Test)\.java$")
SCHEMA_FILE_RE = re.compile(r"\.(avsc|avdl|proto)$", re.I)
FIELD_RE = re.compile(r"^\s*(?:private|protected|public)\s+(?!static\b)(?:final\s+)?[\w<>\[\],.? ]+?\s+(\w+)"
                      r"\s*(?:=[^;]*)?;\s*(?://.*)?$")
SNAPSHOT_RE = re.compile(r"-SNAPSHOT\b")
POM_VERSION_RE = re.compile(r"<([\w-]+[.-]version|version[.-][\w.-]+)>\s*([^<\s]+)\s*</\1>", re.I)
GRADLE_VERSION_RE = re.compile(r"""^\s*([\w.-]*[vV]ersion[\w.-]*)\s*[=:]\s*["']?([\w.+-]+)["']?\s*$""")
TOML_VERSION_RE = re.compile(r"""^\s*([\w.-]+)\s*=\s*["']([\w.+-]+)["']\s*$""")
PLATFORM_VERSION_RE = re.compile(r"(?i)spring[-.]?boot|java|jdk|kotlin")

# ---------------------------------------------------------------- sensitive areas

AREA_WORDS = (
    ("tiền/giá", {"payment", "payments", "refund", "wallet", "balance", "settle", "settlement", "invoice",
                  "billing", "fee", "fees", "pricing", "price", "prices", "charge", "money", "budget", "cashback"}),
    ("gửi ra ngoài", {"mail", "email", "sms", "otp", "notification", "notifications", "notify", "notifier",
                      "fcm", "firebase", "zalo"}),
    ("job định kỳ", {"scheduler", "cron", "job", "jobs", "quartz"}),
)
AREA_LINE_RULES = (
    ("phân quyền", re.compile(r"permitAll\s*\(|hasRole|hasAnyRole|hasAuthority|hasAnyAuthority|@PreAuthorize"
                              r"|@Secured|@RolesAllowed")),
    ("job định kỳ", re.compile(r"@Scheduled\b|@SchedulerLock\b|\bcron\s*=")),
    ("xóa dữ liệu", re.compile(r"\.(?:delete|deleteAll|deleteBy\w*|deleteAllBy\w*|deleteInBatch|deleteAllInBatch"
                               r"|removeBy\w*)\s*\(|(?i:\bdelete\s+from\b|\btruncate\s+table\b)")),
    ("gửi ra ngoài", re.compile(r"JavaMailSender|\bsend(?:Mail|Email|Sms|Otp|Notification|Push)\w*\s*\(")),
)
DELETE_KINDS = {"sql-drop", "sql-truncate", "sql-delete-no-where", "lb-delete", "lb-drop", "lb-drop-guarded"}

#: Net findings carried as flags by every commit that touches the file.
FILE_FLAG_KINDS = ps.FILE_LEVEL_KINDS | {"config-target-env-file", "message-field-removed", "message-field-changed",
                                         "message-schema-changed"}


def run(repo, args, check=True):
    return ps.run_git(repo, args, check)


# ---------------------------------------------------------------- refs and commits

def check_repo(repo):
    if not run(repo, ["rev-parse", "--git-dir"], check=False).strip():
        raise ps.ScanError(f"không phải git repository: {os.path.abspath(repo)}")


def resolve_branch(repo, name):
    name = (name or "").strip()
    if not name:
        raise ps.ScanError("tên nhánh rỗng")
    candidates = [name]
    if not name.startswith(("origin/", "refs/")) and name != "HEAD" and not re.fullmatch(r"[0-9a-f]{7,40}", name):
        candidates.insert(0, f"origin/{name}")
    for ref in candidates:
        sha = run(repo, ["rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"], check=False).strip()
        if sha:
            return ref, sha
    raise ps.ScanError(f"không tìm thấy nhánh {name} (thử: git fetch origin {name})")


def parse_log(text):
    rows = []
    for record in text.split("\x1e"):
        record = record.strip("\n")
        if not record.strip():
            continue
        parts = record.split("\x1f")
        if len(parts) < 6:
            continue
        rows.append({"mark": parts[0].strip(), "sha": parts[1], "short": parts[2], "author": parts[3],
                     "date": parts[4], "subject": parts[5].strip(),
                     "body_full": parts[6].strip() if len(parts) > 6 else ""})
    return rows


def norm_subject(subject):
    return re.sub(r"\s+", " ", subject).strip().lower()


def tickets_of(subject):
    out = []
    for key in TICKET_RE.findall(subject):
        if key.split("-")[0] not in NOT_TICKETS and key not in out:
            out.append(key)
    return out


def brief(c):
    body = ps.redact(c.get("body_full", ""))
    body = "\n".join(line for line in body.splitlines() if not CHERRY_RE.search(line)).strip()
    return {"sha": c["sha"], "short": c["short"], "date": c["date"], "author": c["author"],
            "subject": c["subject"], "body": body[:300], "tickets": tickets_of(c["subject"])}


def commit_date(repo, sha):
    return run(repo, ["log", "-1", "--format=%ad", "--date=short", sha], check=False).strip()


# ---------------------------------------------------------------- per-file release checks

def key_value(path, text):
    if text.lstrip().startswith("#"):
        return None, None
    if re.search(r"\.ya?ml$", path, re.I):
        m = ps.YAML_KEY_RE.match(text)
        return (m.group(2), (m.group(4) or "").strip()) if m else (None, None)
    m = ps.PROPERTIES_KEY_RE.match(text)
    return (m.group(1), m.group(2).strip()) if m else (None, None)


def env_side(path, target_envs):
    """("target", env) / ("source", env) for an environment-specific file, else (None, None)."""
    if ps.TEST_RE.search(path):
        return None, None
    base = os.path.basename(path).lower()
    tokens = {seg.lower() for seg in path.split("/")[:-1]}
    tokens.add(base.rsplit(".", 1)[0])
    m = ENV_NAME_RE.match(base) or DOTENV_RE.match(base)
    if m:
        tokens.add(m.group(1))
        tokens.update(m.group(1).split("-"))
    hit = [e for e in target_envs if e in tokens]
    if hit:
        return "target", hit[0]
    hit = sorted(tokens & SOURCE_ENVS)
    if hit:
        return "source", hit[0]
    return None, None


def message_findings(path, added, removed):
    def fields(lines):
        out = {}
        for text in lines:
            m = FIELD_RE.match(text)
            if m:
                out[m.group(1)] = re.sub(r"\s+", " ", text.strip())
        return out

    new = fields(t for _, t in added)
    old = fields(removed)
    gone = sorted(set(old) - set(new))
    changed = sorted(k for k in set(old) & set(new) if old[k] != new[k])
    fresh = sorted(set(new) - set(old))
    out = []
    if gone:
        out.append(ps.finding("medium", "message-field-removed", path, None,
                              f"lớp message bỏ hoặc đổi tên field ({', '.join(gone[:8])}): producer và consumer "
                              "deploy lệch thời điểm sẽ lỗi deserialize"))
    if changed:
        out.append(ps.finding("medium", "message-field-changed", path, None,
                              f"lớp message đổi khai báo field ({', '.join(changed[:8])}): kiểm tra đổi kiểu"))
    if fresh and not gone and not changed:
        out.append(ps.finding("info", "message-field-added", path, None,
                              f"lớp message thêm field ({', '.join(fresh[:8])}): consumer bản cũ phải bỏ qua field lạ"))
    return out


def build_findings(path, added, removed):
    out = []
    for line_no, text in added:
        if SNAPSHOT_RE.search(text):
            out.append(ps.finding("medium", "dependency-snapshot", path, line_no,
                                  "phụ thuộc -SNAPSHOT trên nhánh phát hành: build không tái lập; chốt bản release "
                                  "trước production"))
    if path.endswith("pom.xml"):
        rx, search = POM_VERSION_RE, True
    elif path.endswith(".toml"):
        rx, search = TOML_VERSION_RE, False
    else:
        rx, search = GRADLE_VERSION_RE, False

    def versions(pairs):
        found = {}
        for line_no, text in pairs:
            m = rx.search(text) if search else rx.match(text)
            if m:
                found.setdefault(m.group(1), (line_no, m.group(2)))
        return found

    new = versions(added)
    old = versions((None, t) for t in removed)
    for name, (line_no, value) in sorted(new.items()):
        before = old.get(name, (None, None))[1]
        if before == value:
            continue
        platform = PLATFORM_VERSION_RE.search(name) is not None
        detail = f"`{name}`: {before} → {value}" if before else f"`{name}` = {value} (mới)"
        if platform:
            detail += "; đổi nền tảng: build và chạy lại toàn bộ test"
        out.append(ps.finding("medium" if platform else "info", "version-changed", path, line_no, detail))
    return out


def extra_findings(path, added, removed, cats, target_envs):
    """Release checks pr_scan does not have. Returns (findings, required keys)."""
    out, required = [], []
    if "test" in cats:
        return out, required
    removed_text = "\n".join(removed)
    if "config" in cats or "deploy" in cats:
        side, env = env_side(path, target_envs)
        if side == "target":
            out.append(ps.finding("medium", "config-target-env-file", path, None,
                                  f"file cấu hình riêng `{env}` đổi: có hiệu lực trên môi trường đích ngay khi deploy"))
        elif side == "source":
            out.append(ps.finding("info", "config-source-env-file", path, None,
                                  f"file cấu hình riêng `{env}`: không chạy trên môi trường đích; thay đổi tương ứng "
                                  "cho đích (nếu cần) phải nằm ở file khác hoặc repo cấu hình"))
        if ps.CONFIG_TEXT_RE.search(path) and side != "source":
            for line_no, text in added:
                key, value = key_value(path, text)
                if not key or not value:
                    continue
                if ps.CONFIG_RE.search(path):
                    for name in PLACEHOLDER_RE.findall(value):
                        if "${" + name + "}" not in removed_text:
                            required.append({"key": name.strip(), "file": path, "line": line_no,
                                             "how": f"giá trị của `{key}`"})
                if side is None and "${" not in value and STAGING_VALUE_RE.search(value):
                    out.append(ps.finding("medium", "config-staging-value", path, line_no,
                                          f"`{key}` có giá trị giống staging/dev/localhost trong file dùng chung: "
                                          "chạy cả trên môi trường đích (giá trị không in ra)"))
    if path.endswith(".java"):
        for line_no, text in added:
            for name in VALUE_ANNOTATION_RE.findall(text):
                if "${" + name + "}" not in removed_text:
                    required.append({"key": name.strip(), "file": path, "line": line_no, "how": "@Value"})
            if TOPIC_DECL_RE.search(text):
                out.append(ps.finding("info", "kafka-topic-declared", path, line_no,
                                      "topic khai báo trong code (NewTopic/TopicBuilder): cụm tắt auto-create hoặc "
                                      "thiếu quyền thì app lỗi khi khởi động"))
        if MESSAGE_PATH_RE.search(path) and not NOT_MESSAGE_RE.search(path):
            out.extend(message_findings(path, added, removed))
    if SCHEMA_FILE_RE.search(path):
        out.append(ps.finding("medium", "message-schema-changed", path, None,
                              "schema message đổi: kiểm tra tương thích ngược giữa producer và consumer"))
    if "build" in cats:
        out.extend(build_findings(path, added, removed))
    return out, required


def camel_words(name):
    stem = name.rsplit(".", 1)[0]
    return {w.lower() for w in re.findall(r"[A-Z]+(?![a-z])|[A-Z]?[a-z0-9]+", stem)}


def areas_of(path, added, removed, cats, kinds):
    if "test" in cats:
        return set()
    out = set()
    if path.endswith(".java"):
        words = camel_words(os.path.basename(path))
        for label, vocabulary in AREA_WORDS:
            if words & vocabulary:
                out.add(label)
        lines = [t for _, t in added] + list(removed)
        for label, rx in AREA_LINE_RULES:
            if any(rx.search(t) for t in lines):
                out.add(label)
    if "security" in cats:
        out.add("phân quyền")
    if kinds & DELETE_KINDS:
        out.add("xóa dữ liệu")
    return out


# ---------------------------------------------------------------- packaged config keys

def flatten_yaml(text):
    keys, stack = [], []
    for raw in text.splitlines():
        stripped = raw.strip()
        if stripped == "---":
            stack = []
            continue
        if not stripped or stripped.startswith("#"):
            continue
        m = re.match(r"^(\s*)(-\s+)?([\w.\-\[\]\"']+)\s*:(\s|$)", raw)
        if not m:
            continue
        indent = len(m.group(1)) + len(m.group(2) or "")
        while stack and stack[-1][0] >= indent:
            stack.pop()
        stack.append((indent, m.group(3).strip("\"'")))
        keys.append(".".join(k for _, k in stack))
    return keys


def flatten_properties(text):
    keys = []
    for raw in text.splitlines():
        if raw.lstrip().startswith(("#", "!")):
            continue
        m = ps.PROPERTIES_KEY_RE.match(raw)
        if m:
            keys.append(m.group(1))
    return keys


def packaged_keys(repo, rev, target_envs):
    """canonical key -> first packaged config file (default profile or a target profile) that sets it."""
    keys = {}
    for path in run(repo, ["ls-tree", "-r", "--name-only", rev], check=False).splitlines():
        if not PACKAGED_RE.search(path) or env_side(path, target_envs)[0] == "source":
            continue
        text = run(repo, ["show", f"{rev}:{path}"], check=False)
        names = flatten_yaml(text) if re.search(r"\.ya?ml$", path, re.I) else flatten_properties(text)
        for name in names:
            keys.setdefault(canonical(name), path)
    return keys


# ---------------------------------------------------------------- trial merge and the net change

def trial_merge(repo, target_sha, source_sha):
    if ps.git_version(repo) < (2, 38):
        return {"status": "unknown", "tree": None, "conflicts": [], "reason": "git < 2.38 không có merge-tree --write-tree"}
    proc = subprocess.run(
        ["git", "-C", repo, "merge-tree", "--write-tree", "--name-only", "--no-messages", target_sha, source_sha],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    lines = proc.stdout.splitlines()
    if proc.returncode in (0, 1) and lines and re.fullmatch(r"[0-9a-f]{40,64}", lines[0].strip()):
        conflicts = []
        for line in lines[1:]:
            if line.strip() and line not in conflicts:
                conflicts.append(line)
        return {"status": "clean" if proc.returncode == 0 else "conflict", "tree": lines[0].strip(),
                "conflicts": conflicts}
    first = (proc.stderr or "").strip().splitlines()[:1]
    return {"status": "unknown", "tree": None, "conflicts": [],
            "reason": first[0] if first else f"exit {proc.returncode}"}


def analyze_change(repo, frm, to, target_sha, merge_base, source_sha, conflicts, ctx, target_envs):
    name_status = ps.parse_name_status(run(repo, ["diff", "--no-renames", "--name-status", frm, to]))
    patches = {f["path"]: f for f in ps.parse_patch(run(repo, ["diff", "--no-renames", "-U0", frm, to]))}
    for path in conflicts:   # the merged file holds conflict markers: judge what the source side wants
        side = ps.parse_patch(run(repo, ["diff", "--no-renames", "-U0", merge_base, source_sha, "--", path],
                                  check=False))
        if side:
            patches[path] = side[0]

    files, findings, config_changes, dependencies, required = [], [], [], [], []
    statements = {}
    for path, f in patches.items():
        res = ps.analyze_content(path, f["added"], f["removed"], ctx)
        extra, req = extra_findings(path, f["added"], f["removed"], res["categories"], target_envs)
        files.append({"path": path, "status": name_status.get(path, "M"), "categories": res["categories"],
                      "changelog_index": res["changelog_index"], "conflict": path in conflicts,
                      "added": len(f["added"]), "removed": len(f["removed"])})
        findings.extend(res["findings"])
        findings.extend(extra)
        config_changes.extend(res["config_keys"])
        dependencies.extend(res["dependencies"])
        required.extend(req)
        if res["statements"]:
            statements[path] = res["statements"]

    for f in files:
        if "migration" not in f["categories"]:
            continue
        if f["changelog_index"]:
            findings.extend(ps.changelog_index_findings(repo, to, f, patches[f["path"]]))
        elif f["status"] in ("M", "D"):
            findings.append(ps.finding("high", "migration-existing-changed", f["path"], None,
                                       "migration đã có trên nhánh đích bị sửa hoặc xóa: đổi checksum, môi trường "
                                       "đã chạy sẽ lỗi khi khởi động; tạo migration mới thay vì sửa"))
    findings.extend(ps.flyway_findings(repo, target_sha, to, files))
    findings.extend(ps.not_included_findings(repo, to, files))

    packaged = packaged_keys(repo, to, target_envs) if required else {}
    uncovered = []
    for r in required:
        where = packaged.get(canonical(r["key"]))
        r["packaged_in"] = where
        if where:
            continue
        if r["key"] not in uncovered:
            uncovered.append(r["key"])
        findings.append(ps.finding("medium", "config-required-key", r["file"], r["line"],
                                   f"`{r['key']}` ({r['how']}) không có default và không có trong config đóng gói: "
                                   "môi trường đích phải khai báo, thiếu thì app không khởi động"))

    kinds = {x["kind"] for x in findings}
    if "kafka-offset-earliest" in kinds and kinds & {"kafka-group-changed", "kafka-listener"}:
        findings.append(ps.finding("high", "kafka-replay-risk", "-", None,
                                   "consumer/group mới cùng auto-offset-reset=earliest: lần chạy đầu đọc lại toàn bộ "
                                   "lịch sử topic còn trong retention; chốt offset trước khi deploy"))
    findings.sort(key=lambda x: (ps.SEVERITY_ORDER[x["severity"]], x["file"], x["line"] or 0))
    findings = ps.collapse(findings)

    migrations = [{"file": f["path"], "status": f["status"], "changelog_index": f["changelog_index"],
                   "statements": statements.get(f["path"], {})} for f in files if "migration" in f["categories"]]
    return {"files": files, "findings": findings, "config_changes": config_changes, "dependencies": dependencies,
            "migrations": migrations, "required_keys": required, "uncovered_keys": uncovered}


def commit_detail(repo, c, ctx, target_envs):
    patch = ps.parse_patch(run(repo, ["show", "--format=", "--no-renames", "-U0", c["sha"]], check=False))
    cats, kinds, areas, paths, keys = set(), set(), set(), [], []
    added = removed = 0
    for pf in patch:
        res = ps.analyze_content(pf["path"], pf["added"], pf["removed"], ctx)
        extra, req = extra_findings(pf["path"], pf["added"], pf["removed"], res["categories"], target_envs)
        file_kinds = {x["kind"] for x in res["findings"] + extra}
        cats.update(res["categories"])
        kinds.update(file_kinds)
        areas.update(areas_of(pf["path"], pf["added"], pf["removed"], res["categories"], file_kinds))
        keys.extend(r["key"] for r in req)
        paths.append(pf["path"])
        added += len(pf["added"])
        removed += len(pf["removed"])
    return {**brief(c), "files": paths, "categories": sorted(cats), "flags": sorted(kinds), "areas": sorted(areas),
            "added": added, "removed": removed, "_keys": keys}


# ---------------------------------------------------------------- compare

def repo_name(repo):
    top = run(repo, ["rev-parse", "--show-toplevel"], check=False).strip()
    if top:
        return os.path.basename(top)
    git_dir = run(repo, ["rev-parse", "--absolute-git-dir"], check=False).strip()
    name = os.path.basename(git_dir)
    if name == ".git":
        name = os.path.basename(os.path.dirname(git_dir))
    return re.sub(r"\.git$", "", name) or "repo"


def patch_ids(repo, shas):
    """sha -> stable patch id, to pair the commits git log --cherry-mark marks as equivalent."""
    if not shas:
        return {}
    log = subprocess.run(["git", "-C", repo, "log", "--no-walk=unsorted", "-p", "--no-renames", "--format=commit %H",
                          *shas], capture_output=True, text=True, encoding="utf-8", errors="replace")
    ids = subprocess.run(["git", "-C", repo, "patch-id", "--stable"], input=log.stdout,
                         capture_output=True, text=True, encoding="utf-8", errors="replace")
    out = {}
    for line in ids.stdout.splitlines():
        parts = line.split()
        if len(parts) == 2:
            out[parts[1]] = parts[0]
    return out


def classify(src, tgt, ids=None):
    """Split source-only commits into already on target / maybe / candidates; return target-only too."""
    ids = ids or {}
    by_subject, by_patch = {}, {}
    for t in tgt:
        by_subject.setdefault(norm_subject(t["subject"]), []).append(t)
        if t["mark"] == "=" and t["sha"] in ids:
            by_patch.setdefault(ids[t["sha"]], []).append(t)
    picked = []   # (source sha prefix, target commit) from "cherry picked from commit" lines
    for t in tgt:
        for ref in CHERRY_RE.findall(t["body_full"]):
            picked.append((ref, t))
    used = set()

    def take(candidates):
        for t in candidates:
            if t["sha"] not in used:
                used.add(t["sha"])
                return t
        return None

    already, maybe, rest = [], [], []
    for c in src:
        same = by_subject.get(norm_subject(c["subject"]), [])
        if c["mark"] == "=":
            pair = take(by_patch.get(ids.get(c["sha"]), [])) or take([t for t in same if t["mark"] == "="])
            already.append({**brief(c), "how": "cùng nội dung", "target": pair["short"] if pair else None})
            continue
        pair = take([t for ref, t in picked if c["sha"].startswith(ref)])
        if pair:
            already.append({**brief(c), "how": "cherry-pick -x", "target": pair["short"]})
            continue
        pair = take([t for t in same if t["mark"] != "="])
        if pair:
            maybe.append((c, pair))
            continue
        rest.append(c)
    target_only = [t for t in tgt if t["mark"] != "=" and t["sha"] not in used]
    return already, maybe, rest, target_only


def pair_reverts(candidates):
    """Remove commit + revert pairs that are both in the batch; mark reverts of something older."""
    removed, pairs = set(), []
    for i, r in enumerate(candidates):
        m_body = REVERT_BODY_RE.search(r["body_full"])
        m_subject = REVERT_SUBJECT_RE.match(r["subject"])
        if not (m_body or m_subject):
            continue
        earlier = [c for c in reversed(candidates[:i]) if c["sha"] not in removed]
        orig = None
        if m_body:
            orig = next((c for c in earlier if c["sha"].startswith(m_body.group(1))), None)
        if orig is None and m_subject:
            orig = next((c for c in earlier if c["subject"] == m_subject.group(1)), None)
        if orig is not None:
            removed.update({orig["sha"], r["sha"]})
            pairs.append({"commit": brief(orig), "reverted_by": brief(r)})
        else:
            r["_revert_of_older"] = True
    return [c for c in candidates if c["sha"] not in removed], pairs


def compare(repo, source="staging", target="release", dialect_flag="auto", merge=True,
            target_envs=DEFAULT_TARGET_ENVS, today=None):
    check_repo(repo)
    source_ref, source_sha = resolve_branch(repo, source)
    target_ref, target_sha = resolve_branch(repo, target)
    merge_base = run(repo, ["merge-base", target_sha, source_sha], check=False).strip()
    if not merge_base:
        raise ps.ScanError("hai nhánh không có commit chung")
    dialect, dialect_source = ps.resolve_dialect(repo, source_sha, dialect_flag)
    ctx = {"dialect": dialect}
    target_envs = tuple(e.lower() for e in target_envs)

    sym = f"{target_sha}...{source_sha}"
    common = ["log", "--no-merges", "--cherry-mark", "--reverse", "--date=short", f"--format={LOG_FORMAT}"]
    src = parse_log(run(repo, common + ["--right-only", sym]))
    tgt = parse_log(run(repo, common + ["--left-only", sym]))
    merges_skipped = int(run(repo, ["rev-list", "--merges", "--count", f"{target_sha}..{source_sha}"],
                             check=False).strip() or 0)

    trial = trial_merge(repo, target_sha, source_sha) if merge else \
        {"status": "skipped", "tree": None, "conflicts": []}
    if trial["tree"]:
        frm, to, basis = target_sha, trial["tree"], "merge-result"
    else:
        frm, to, basis = merge_base, source_sha, "merge-base"
    change = analyze_change(repo, frm, to, target_sha, merge_base, source_sha, trial["conflicts"], ctx, target_envs)
    net_paths = {f["path"] for f in change["files"]}
    uncovered = set(change["uncovered_keys"])
    conflicts = set(trial["conflicts"])
    file_flags = {}
    for x in change["findings"]:
        if x["kind"] in FILE_FLAG_KINDS:
            file_flags.setdefault(x["file"], set()).add(x["kind"])

    def detail(c):
        d = commit_detail(repo, c, ctx, target_envs)
        flags = set(d["flags"])
        for p in d["files"]:
            flags.update(file_flags.get(p, ()))
            if p in conflicts:
                flags.add("merge-conflict")
        if uncovered & set(d.pop("_keys")):
            flags.add("config-required-key")
        if c.get("_revert_of_older"):
            flags.add("revert-older-change")
        d["flags"] = sorted(flags)
        d["net_effect"] = bool(net_paths & set(d["files"]))
        return d

    ids = patch_ids(repo, [c["sha"] for c in src + tgt if c["mark"] == "="])
    already, maybe_pairs, candidates, target_only_raw = classify(src, tgt, ids)
    candidates, reverted = pair_reverts(candidates)
    missing, no_effect = [], []
    for c in candidates:
        d = detail(c)
        (missing if d["net_effect"] else no_effect).append(d)
    for d in no_effect:
        for key in ("flags", "areas", "categories"):
            d.pop(key, None)
    maybe = []
    for c, t in maybe_pairs:
        d = detail(c)
        maybe.append({**d, "target": brief(t)})
    # Merging the target back into the source: the same merged tree, seen from the source.
    back_paths = set(run(repo, ["diff", "--no-renames", "--name-only", source_sha, to], check=False).splitlines()) \
        if basis == "merge-result" else None
    target_only = []
    for t in target_only_raw:
        d = detail(t)
        d["cherry_of"] = (CHERRY_RE.findall(t["body_full"]) or [None])[0]
        d.pop("net_effect", None)
        d["back_merge_effect"] = bool(back_paths & set(d["files"])) if back_paths is not None else None
        target_only.append(d)

    # Tickets with commits both on the target and still missing.
    on_target = {}
    for item in already + [d["target"] for d in maybe] + target_only:
        for key in tickets_of(item["subject"]):
            on_target.setdefault(key, []).append(item.get("target") or item["short"])
    partial = {}
    for d in missing:
        for key in d["tickets"]:
            if key in on_target:
                partial.setdefault(key, {"ticket": key, "missing": [], "on_target": sorted(set(on_target[key]))})
                partial[key]["missing"].append(d["short"])

    def touching(tip, path):
        out = run(repo, ["log", "--no-merges", "--format=%h", f"{merge_base}..{tip}", "--", path], check=False)
        return out.split()[:10]

    conflict_rows = [{"file": p, "source_commits": touching(source_sha, p), "target_commits": touching(target_sha, p)}
                     for p in trial["conflicts"]]

    target_added = ps.parse_name_status(run(repo, ["diff", "--no-renames", "--name-status", merge_base, target_sha]))
    source_tree = set(run(repo, ["ls-tree", "-r", "--name-only", source_sha], check=False).splitlines())
    target_only_migrations = [p for p, s in sorted(target_added.items())
                              if s == "A" and "migration" in ps.categorize(p) and p not in source_tree]

    env_keys = list(change["uncovered_keys"])
    if any(x["kind"].startswith("kafka-") for x in change["findings"]):
        env_keys.append("spring.kafka.consumer.auto-offset-reset")

    category_counts = Counter(c for f in change["files"] for c in f["categories"])
    today = today or datetime.date.today().strftime("%Y%m%d")
    name = f"compare-{ps.slug(source_ref)}-to-{ps.slug(target_ref)}-{ps.slug(repo_name(repo))}-{today}.md"
    merge_info = {k: v for k, v in trial.items() if k != "tree"}

    return {
        "range": {
            "source": source, "source_ref": source_ref, "source_sha": source_sha,
            "source_date": commit_date(repo, source_sha),
            "target": target, "target_ref": target_ref, "target_sha": target_sha,
            "target_date": commit_date(repo, target_sha),
            "merge_base": merge_base, "merge_base_date": commit_date(repo, merge_base),
            "current_branch": run(repo, ["rev-parse", "--abbrev-ref", "HEAD"], check=False).strip(),
            "dialect": dialect, "dialect_source": dialect_source,
            "merge": merge_info,
            "diff_basis": basis, "diff_from": frm, "diff_to": to,
            "files": len(change["files"]),
            "added": sum(f["added"] for f in change["files"]),
            "removed": sum(f["removed"] for f in change["files"]),
            "touches_build": category_counts.get("build", 0) > 0,
            "categories": dict(category_counts),
            "counts": {"missing": len(missing), "already_on_target": len(already), "maybe_on_target": len(maybe),
                       "reverted_pairs": len(reverted), "no_net_effect": len(no_effect),
                       "target_only": len(target_only), "merge_commits_skipped": merges_skipped},
            "authors": sorted({d["author"] for d in missing}),
            "first_date": min((d["date"] for d in missing), default=None),
            "last_date": max((d["date"] for d in missing), default=None),
            "target_envs": list(target_envs),
            "env_check_keys": ",".join(env_keys),
            "report_name": name,
        },
        "commits": {"missing": missing, "maybe_on_target": maybe, "already_on_target": already,
                    "reverted": reverted, "no_net_effect": no_effect},
        "target_only": target_only,
        "partial_tickets": list(partial.values()),
        "conflicts": conflict_rows,
        "target_only_migrations": target_only_migrations,
        "findings": change["findings"],
        "config_changes": change["config_changes"],
        "required_keys": change["required_keys"],
        "migrations": change["migrations"],
        "dependencies": change["dependencies"],
        "files": change["files"],
    }


# ---------------------------------------------------------------- output

def render_text(report):
    r = report["range"]
    n = r["counts"]
    m = r["merge"]
    merge_line = m["status"] + (f": {', '.join(m['conflicts'])}" if m.get("conflicts") else "") \
        + (f" ({m['reason']})" if m.get("reason") else "")
    lines = [
        f"Source: {r['source_ref']} ({r['source_sha'][:8]}, {r['source_date']})  ->  "
        f"Target: {r['target_ref']} ({r['target_sha'][:8]}, {r['target_date']})  "
        f"merge-base {r['merge_base'][:8]} ({r['merge_base_date']})",
        f"Commits: missing {n['missing']} | already on target {n['already_on_target']} | "
        f"maybe on target {n['maybe_on_target']} | reverted pairs {n['reverted_pairs']} | "
        f"no net effect {n['no_net_effect']} | target only {n['target_only']} | "
        f"merge commits skipped {n['merge_commits_skipped']}",
        f"Trial merge: {merge_line}   Diff basis: {r['diff_basis']} "
        f"({'target -> merge result' if r['diff_basis'] == 'merge-result' else 'merge-base -> source'})",
        f"Net change: {r['files']} files +{r['added']} -{r['removed']}   Build files: "
        f"{'yes' if r['touches_build'] else 'no'}   Database: {r['dialect']} ({r['dialect_source']})",
        f"Env check keys: {r['env_check_keys'] or '-'}",
        f"Report: review-reports/{r['report_name']}",
        "",
        "Missing commits:",
    ]

    def row(d):
        extra = []
        if d.get("categories"):
            extra.append("[" + ", ".join(d["categories"]) + "]")
        if d.get("flags"):
            extra.append("flags: " + ", ".join(d["flags"]))
        if d.get("areas"):
            extra.append("areas: " + ", ".join(d["areas"]))
        return f"  {d['short']} {d['date']} {d['author']}: {d['subject']}  " + "  ".join(extra)

    c = report["commits"]
    lines += [row(d) for d in c["missing"]] or ["  -"]
    if c["maybe_on_target"]:
        lines += ["", "Maybe on target (same subject, different patch):"]
        lines += [f"  {d['short']} ~ {d['target']['short']}  {d['subject']}  "
                  f"(merge still changes its files: {'yes' if d['net_effect'] else 'no'})" for d in c["maybe_on_target"]]
    if c["already_on_target"]:
        lines += ["", "Already on target:"]
        lines += [f"  {d['short']} = {d['target'] or '?'} ({d['how']})  {d['subject']}" for d in c["already_on_target"]]
    if c["reverted"]:
        lines += ["", "Reverted inside the batch:"]
        lines += [f"  {p['commit']['short']} reverted by {p['reverted_by']['short']}  {p['commit']['subject']}"
                  for p in c["reverted"]]
    if c["no_net_effect"]:
        lines += ["", "No net effect (merge changes none of their files):"]
        lines += [f"  {d['short']} {d['date']} {d['subject']}" for d in c["no_net_effect"]]
    if report["target_only"]:
        lines += ["", "Only on target (merge back into the source):"]
        lines += [row(d) + (f"  cherry-pick of {d['cherry_of'][:8]}" if d.get("cherry_of") else "")
                  + ("  (back-merge changes nothing: content already in source)"
                     if d.get("back_merge_effect") is False else "")
                  for d in report["target_only"]]
    if report["partial_tickets"]:
        lines += ["", "Tickets partly on target:"]
        lines += [f"  {t['ticket']}: missing {', '.join(t['missing'])}; on target {', '.join(t['on_target'])}"
                  for t in report["partial_tickets"]]
    if report["conflicts"]:
        lines += ["", "Conflicts:"]
        lines += [f"  {x['file']}  source: {', '.join(x['source_commits']) or '-'}  "
                  f"target: {', '.join(x['target_commits']) or '-'}" for x in report["conflicts"]]
    if report["target_only_migrations"]:
        lines += ["", "Migrations only on target:"] + [f"  {p}" for p in report["target_only_migrations"]]
    lines += ["", f"Findings ({len(report['findings'])}):"]
    lines += [f"  [{x['severity']}] {x['kind']} {ps.where_of(x)} - {x['detail']}" for x in report["findings"]]
    if report["config_changes"]:
        lines += ["", "Config key changes (by key name):"]
        lines += [f"  {x['change']} {x['key']}  ({x['file']})" for x in report["config_changes"]]
    if report["migrations"]:
        lines += ["", "Migrations:"]
        for x in report["migrations"]:
            stm = ", ".join(f"{k}:{v}" for k, v in x["statements"].items()) or "-"
            lines.append(f"  {x['status']} {x['file']}  [{stm}]" + ("  (changelog index)" if x["changelog_index"] else ""))
    return "\n".join(lines) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description="Compare two branches before merging the source into the target.")
    parser.add_argument("--source", default="staging", help="branch to merge from (default: staging)")
    parser.add_argument("--target", default="release", help="branch to merge into (default: release)")
    parser.add_argument("--repo", default=".", help="repository directory (default: current)")
    parser.add_argument("--format", choices=["json", "text"], default="json", help="what to print")
    parser.add_argument("--out", help="also write the JSON report to this file")
    parser.add_argument("--dialect", choices=("auto",) + ps.DIALECTS, default="auto",
                        help="database for the SQL rules; auto reads JDBC URLs and drivers in the repository")
    parser.add_argument("--target-envs", default=",".join(DEFAULT_TARGET_ENVS),
                        help="environment names whose config files run with the target branch")
    parser.add_argument("--no-merge-check", action="store_true",
                        help="skip the trial merge; the impact is then judged on merge-base..source")
    parser.add_argument("--patch-file", help="also write the net diff (-U5) to this file")
    args = parser.parse_args(argv)

    envs = [e.strip() for e in args.target_envs.split(",") if e.strip()]
    written = None
    try:
        report = compare(args.repo, args.source, args.target, args.dialect, not args.no_merge_check, envs)
        r = report["range"]
        for written, content in ((args.patch_file, lambda: run(args.repo, ["diff", "--no-renames", "-U5",
                                                                           r["diff_from"], r["diff_to"]])),
                                 (args.out, lambda: json.dumps(report, ensure_ascii=False, indent=2) + "\n")):
            if written:
                os.makedirs(os.path.dirname(os.path.abspath(written)), exist_ok=True)
                with open(written, "w", encoding="utf-8") as fh:
                    fh.write(content())
    except ps.ScanError as exc:
        print(f"branch_compare: {exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"branch_compare: không ghi được {written}: {exc.strerror or exc}", file=sys.stderr)
        return 2

    if args.format == "text":
        sys.stdout.write(render_text(report))
    else:
        json.dump(report, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
