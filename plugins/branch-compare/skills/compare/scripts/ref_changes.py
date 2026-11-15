#!/usr/bin/env python3
"""What changed between two revisions of a Spring service: tags, branches or commits.

For a developer: the commits in between (grouped, reverts and duplicates set aside), then the state at
the second revision compared with the first, area by area:

- API: endpoints served (@RestController) and called (@FeignClient) at both revisions, compared by
  method and path: added, removed, changed (headers, params, body, return type, security), with what
  breaks an existing caller; request/response/event classes with fields added, removed or changed;
- business code: enums (constants), scheduled jobs, Kafka listeners, changed logic files with the
  sensitive areas they touch (money, permissions, jobs, outgoing messages, deletes);
- config: every key of the packaged config files (application*.yml/properties) with its value before
  and after (secrets masked), keys the code now needs without a default, other config files;
- database: migrations added or changed with the operations they run (tables, columns, indexes, data
  changes, Liquibase changesets and contexts), entity tables and columns before and after, and
  entity/migration mismatches;
- dependencies: Maven/Gradle coordinates and versions before and after;
- plus spring-pr-review's rules on the diff (pr_scan.py) and branch_compare's release checks.

Read-only: reads objects with git, never checks out. Secret values are never printed.

  ref_changes.py --from v1.4.0 --to v1.5.0 --out "$TMP/changes.json" --format text
  ref_changes.py --from auto --to release            # previous tag of release -> release
  ref_changes.py --discover ~/code/services --from staging --to release
  ref_changes.py --cross "$TMP/a.json" "$TMP/b.json" --format text      # links between services

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
import branch_compare as bc  # noqa: E402
import java_inventory as ji  # noqa: E402
import pr_scan as ps  # noqa: E402

run = bc.run

CONFIG_FILE_RE = re.compile(
    r"(^|/)src/main/resources/(config/)?(application|bootstrap)([-.][\w-]+)?\.(ya?ml|properties)$", re.I)
JAVA_HINT_RE = r"@(Rest)?Controller\b|@FeignClient\b|@RequestMapping\b"
MONGOCK_RE = re.compile(r"@(ChangeUnit|ChangeLog|ChangeSet|Execution)\b")
MONGO_INDEX_RE = re.compile(r"\.(createIndex|dropIndex|createCollection|dropCollection|updateMany|deleteMany|insertMany|"
                            r"insertOne|renameCollection)\s*\(|@(Indexed|CompoundIndex|TextIndexed)\b")
SECRET_URL_RE = re.compile(r"(://[^/\s:@]+:)[^/\s@]+@")
PLACEHOLDER_DEFAULT_RE = re.compile(r"(\$\{[^:}]+:)[^}]+\}")
VALUE_LIMIT = 160


# ---------------------------------------------------------------- refs

def resolve_ref(repo, name):
    """-> (ref, sha, kind) for a tag, a branch (origin/<name> first) or a commit."""
    name = (name or "").strip()
    if not name:
        raise ps.ScanError("ref rỗng")
    tag = run(repo, ["rev-parse", "--verify", "--quiet", f"refs/tags/{name}^{{commit}}"], check=False).strip()
    if tag:
        return name, tag, "tag"
    try:
        ref, sha = bc.resolve_branch(repo, name)
    except ps.ScanError:
        raise ps.ScanError(f"không tìm thấy tag/nhánh/commit {name} "
                           f"(thử: git fetch origin --tags {name})") from None
    if re.fullmatch(r"[0-9a-f]{7,40}", name) and sha.startswith(name):
        return name, sha, "commit"
    return ref, sha, "branch" if ref != "HEAD" else "commit"


def previous_tag(repo, sha):
    """The nearest tag reachable from the parent of sha (the release before), or None."""
    out = run(repo, ["describe", "--tags", "--abbrev=0", f"{sha}^"], check=False).strip()
    return out or None


def read_files(repo, rev, paths):
    """{path: text} at rev for the given paths, in one git cat-file call (missing files left out)."""
    paths = [p for p in dict.fromkeys(paths) if p]
    if not paths:
        return {}
    data = "".join(f"{rev}:{p}\n" for p in paths).encode("utf-8")
    proc = subprocess.run(["git", "-C", repo, "cat-file", "--batch"], input=data, capture_output=True)
    out, buf, i = {}, proc.stdout, 0
    for p in paths:
        end = buf.find(b"\n", i)
        if end < 0:
            break
        header = buf[i:end].decode("utf-8", "replace").split()
        i = end + 1
        if len(header) < 3 or header[1] == "missing":
            continue
        size = int(header[2])
        out[p] = buf[i:i + size].decode("utf-8", "replace")
        i += size + 1
    return out


def grep_files(repo, rev, pattern, glob="*.java"):
    out = run(repo, ["grep", "-l", "-E", pattern, rev, "--", glob], check=False)
    prefix = f"{rev}:"
    paths = [line[len(prefix):] if line.startswith(prefix) else line for line in out.splitlines() if line.strip()]
    return [p for p in paths if not ps.TEST_RE.search(p)]     # after the "<rev>:" prefix is cut


# ---------------------------------------------------------------- commits

def commits_between(repo, a, b, ctx, target_envs, net_paths):
    common = ["log", "--no-merges", "--cherry-mark", "--reverse", "--date=short", f"--format={bc.LOG_FORMAT}"]
    new = bc.parse_log(run(repo, common + ["--right-only", f"{a}...{b}"]))
    only_from = bc.parse_log(run(repo, common + ["--left-only", f"{a}...{b}"]))
    equivalent = [bc.brief(c) for c in new if c["mark"] == "="]
    candidates, reverted = bc.pair_reverts([c for c in new if c["mark"] != "="])
    kept, no_effect = [], []
    for c in candidates:
        d = bc.commit_detail(repo, c, ctx, target_envs)
        d.pop("_keys", None)
        if c.get("_revert_of_older"):
            d["flags"] = sorted(set(d["flags"]) | {"revert-older-change"})
        d["net_effect"] = bool(net_paths & set(d["files"]))
        (kept if d["net_effect"] else no_effect).append(d)
    kept, duplicates = bc.split_duplicates(kept, bc.patch_ids(repo, [d["sha"] for d in kept]))
    for d in no_effect:
        for key in ("flags", "areas", "categories"):
            d.pop(key, None)
    merges = int(run(repo, ["rev-list", "--merges", "--count", f"{a}..{b}"], check=False).strip() or 0)
    return {
        "new": kept, "duplicates": duplicates, "equivalent": equivalent, "reverted": reverted,
        "no_net_effect": no_effect,
        "only_in_from": [bc.brief(c) for c in only_from if c["mark"] != "="],
        "merges_skipped": merges,
    }


def tickets(commits):
    groups = {}
    for d in commits:
        for key in d["tickets"] or ["(không ticket)"]:
            groups.setdefault(key, []).append(d["short"])
    return [{"ticket": k, "commits": v} for k, v in groups.items()]


# ---------------------------------------------------------------- config values

def _clean_value(raw):
    v = raw.strip()
    if v[:1] in "\"'":
        q = v[0]
        end = v.find(q, 1)
        return v[1:end] if end > 0 else v[1:]
    return re.sub(r"\s+#.*$", "", v).strip()


def flatten_yaml_values(text):
    """{key: value} of a YAML file. Nested maps join with '.', list items are key[i]; a document with
    spring.config.activate.on-profile (or spring.profiles) suffixes its keys with ' @<profile>'."""
    docs, out, stack, counters = [], {}, [], {}
    block = None   # (path, indent, lines)

    def finish_block():
        nonlocal block
        if block:
            out[block[0]] = " ".join(x.strip() for x in block[2]).strip()
            block = None

    for raw in text.splitlines() + ["---"]:
        if block is not None:
            indent = len(raw) - len(raw.lstrip(" "))
            if raw.strip() and indent > block[1]:
                block[2].append(raw)
                continue
            if not raw.strip():
                continue
            finish_block()
        stripped = raw.strip()
        if stripped in ("---", "..."):
            docs.append(out)
            out, stack, counters = {}, [], {}
            continue
        if not stripped or stripped.startswith("#"):
            continue
        m = re.match(r"^(\s*)(-(?:\s+|$))?(.*)$", raw)
        indent, item, body = len(m.group(1)), m.group(2), m.group(3)
        if item:
            while stack and (stack[-1]["indent"] > indent or (stack[-1]["indent"] == indent and stack[-1]["item"])):
                stack.pop()
            parent = stack[-1]["path"] if stack else ""
            idx = counters.get(parent, 0)
            counters[parent] = idx + 1
            ipath = f"{parent}[{idx}]"
            stack.append({"indent": indent, "path": ipath, "item": True})
            indent += len(item)
            if not re.match(r"^[\w.\-\[\]\"'/]+\s*:(\s|$)", body):
                if body.strip():
                    out[ipath] = _clean_value(body)
                continue
        km = re.match(r"^([\w.\-\[\]\"'/]+)\s*:(?:\s+(.*))?$", body)
        if not km:
            continue
        while stack and stack[-1]["indent"] >= indent:
            stack.pop()
        key = km.group(1).strip("\"'")
        path = f"{stack[-1]['path']}.{key}" if stack else key
        value = (km.group(2) or "").strip()
        if not value or value.startswith("#"):
            stack.append({"indent": indent, "path": path, "item": False})
        elif value in ps.BLOCK_SCALARS:
            block = (path, indent, [])
        elif value.startswith("&"):          # anchor on a map
            stack.append({"indent": indent, "path": path, "item": False})
        else:
            out[path] = _clean_value(value)
    merged = {}
    for doc in docs:
        profile = doc.get("spring.config.activate.on-profile") or doc.get("spring.profiles")
        for k, v in doc.items():
            merged[f"{k} @{profile}" if profile else k] = v
    return merged


def flatten_properties_values(text):
    out, pending = {}, ""
    for raw in text.splitlines():
        line = pending + raw.strip() if pending else raw
        if line.rstrip().endswith("\\") and not line.rstrip().endswith("\\\\"):
            pending = line.rstrip()[:-1]
            continue
        pending = ""
        if line.lstrip().startswith(("#", "!")):
            continue
        m = ps.PROPERTIES_KEY_RE.match(line)
        if m:
            out[m.group(1)] = m.group(2).strip()
    return out


def mask_value(key, value):
    if value is None:
        return None
    leaf = re.split(r"[.\[]", key.split(" @")[0].rstrip("]"))[-1]
    v = SECRET_URL_RE.sub(r"\1***@", value)
    if ps.SECRET_KEY_RE.search(leaf):
        if v.startswith("${"):
            v = PLACEHOLDER_DEFAULT_RE.sub(r"\1<secret>}", v)
        elif v and not v.startswith("ENC(") and not ps.NOT_SECRET_VALUE_RE.match(v):
            v = "<secret>"
    v = ps.redact(v)
    return v if len(v) <= VALUE_LIMIT else v[:VALUE_LIMIT] + "…"


def config_values(path, text):
    if text is None:
        return {}
    return flatten_yaml_values(text) if re.search(r"\.ya?ml$", path, re.I) else flatten_properties_values(text)


def diff_config(path, old_text, new_text):
    old, new = config_values(path, old_text), config_values(path, new_text)
    rows = []
    for key in sorted(set(old) | set(new)):
        a, b = old.get(key), new.get(key)
        if a == b:
            continue
        change = "added" if a is None else "removed" if b is None else "changed"
        rows.append({"file": path, "change": change, "key": key,
                     "old": mask_value(key, a), "new": mask_value(key, b)})
    return rows


def config_snippet(rows, required, kept=None):
    """Copy-ready properties text of the packaged config changes: added and changed keys with their new
    value, removed keys commented out (whole removed list items folded into one line), then the keys the
    code needs that no packaged file sets. Secrets stay masked. kept: {file: keys still set at the newer
    revision}, so that a list item is folded only when nothing of it is left."""
    by_file = {}
    for r in rows:
        by_file.setdefault(r["file"], []).append(r)
    out = []
    for path, items in by_file.items():
        out.append(f"# ===== {path} =====")
        touched = {r["key"] for r in items if r["change"] != "removed"} | set((kept or {}).get(path, ()))
        folded, removed = set(), []
        for r in items:
            if r["change"] != "removed":
                continue
            key = r["key"].split(" @")[0]
            prefix = next((key[:m.end()] for m in re.finditer(r"\[\d+\]", key)
                           if not any(k.startswith(key[:m.end()]) for k in touched)), None)
            if prefix:
                if not any(prefix.startswith(f) for f in folded):
                    folded.add(prefix)
                    removed.append(f"# {prefix}.*")
                continue
            removed.append(f"# {r['key']}")
        for title, change in (("thêm", "added"), ("đổi (giá trị cũ ở comment)", "changed")):
            lines = []
            for r in items:
                if r["change"] != change:
                    continue
                key, _, profile = r["key"].partition(" @")
                line = f"{key}={r['new']}"
                notes = [f"profile {profile}"] if profile else []
                if change == "changed":
                    notes.append(f"cũ: {r['old']}")
                lines.append(line + (f"    # {'; '.join(notes)}" if notes else ""))
            if lines:
                out += [f"# --- {title} ---"] + lines
        if removed:
            out += ["# --- bỏ ---"] + removed
        out.append("")
    needed = []
    for r in required:
        if not r.get("packaged_in") and r["key"] not in needed:
            needed.append(r["key"])
    if needed:
        out += ["# ===== bắt buộc khai báo trên môi trường (code đọc, không có default) ====="]
        out += [f"{k}=" for k in needed] + [""]
    return "\n".join(out).rstrip() + "\n" if out else ""


# ---------------------------------------------------------------- dependencies

def _tag(block, name):
    m = re.search(rf"<{name}>\s*([^<]*?)\s*</{name}>", block)
    return m.group(1) if m else None


def pom_coordinates(text):
    """{label: version} of a pom: parent, dependencies, managed dependencies, plugins, *version properties."""
    out = {}
    if not text:
        return out
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    props = {}
    pm = re.search(r"<properties>(.*?)</properties>", text, re.S)
    if pm:
        props = dict(re.findall(r"<([\w.-]+)>\s*([^<]*?)\s*</\1>", pm.group(1)))

    def resolve(v):
        if not v:
            return "(managed)"
        return re.sub(r"\$\{([^}]+)\}", lambda m: props.get(m.group(1), m.group(0)), v)

    parent = re.search(r"<parent>(.*?)</parent>", text, re.S)
    if parent:
        out[f"parent {_tag(parent.group(1), 'groupId')}:{_tag(parent.group(1), 'artifactId')}"] = \
            resolve(_tag(parent.group(1), "version"))
    managed = re.search(r"<dependencyManagement>(.*?)</dependencyManagement>", text, re.S)
    sections = [("managed ", managed.group(1) if managed else "")]
    rest = text.replace(managed.group(0), "") if managed else text
    sections.append(("", rest))
    for prefix, body in sections:
        for block in re.findall(r"<dependency>(.*?)</dependency>", body, re.S):
            scope = _tag(block, "scope")
            label = f"{prefix}{_tag(block, 'groupId')}:{_tag(block, 'artifactId')}"
            if scope and scope not in ("compile", "import"):
                label += f" ({scope})"
            out[label] = resolve(_tag(block, "version"))
    for block in re.findall(r"<plugin>(.*?)</plugin>", text, re.S):
        label = f"plugin {_tag(block, 'groupId') or 'org.apache.maven.plugins'}:{_tag(block, 'artifactId')}"
        out[label] = resolve(_tag(block, "version"))
    for k, v in props.items():
        if "version" in k.lower():
            out[f"property {k}"] = v
    return out


GRADLE_DEP_RE = re.compile(
    r"""^\s*(implementation|api|compileOnly|runtimeOnly|testImplementation|testRuntimeOnly|annotationProcessor|"""
    r"""kapt|developmentOnly|platform|classpath)\s*\(?\s*(?:platform\s*\(\s*)?["']([^:"']+):([^:"']+)(?::([^"']+))?["']""",
    re.M)


def gradle_coordinates(path, text):
    out = {}
    if not text:
        return out
    if path.endswith(".toml"):
        section = None
        for raw in text.splitlines():
            line = raw.strip()
            if line.startswith("["):
                section = line.strip("[]")
                continue
            m = bc.TOML_VERSION_RE.match(line)
            if m and section in ("versions", None):
                out[f"version {m.group(1)}"] = m.group(2)
            elif section in ("libraries", "plugins") and "=" in line:
                key = line.split("=", 1)[0].strip()
                out[f"{section} {key}"] = squash_value(line.split("=", 1)[1])
        return out
    for m in GRADLE_DEP_RE.finditer(text):
        label = f"{m.group(2)}:{m.group(3)}" + ("" if m.group(1) in ("implementation", "api") else f" ({m.group(1)})")
        out[label] = m.group(4) or "(managed)"
    for line in text.splitlines():
        m = bc.GRADLE_VERSION_RE.match(line)
        if m:
            out[f"property {m.group(1)}"] = m.group(2)
    return out


def squash_value(text):
    return re.sub(r"\s+", " ", text).strip()


def diff_build(path, old_text, new_text):
    if path.endswith("pom.xml"):
        old, new = pom_coordinates(old_text), pom_coordinates(new_text)
    else:
        old, new = gradle_coordinates(path, old_text), gradle_coordinates(path, new_text)
    rows = []
    for label in sorted(set(old) | set(new)):
        a, b = old.get(label), new.get(label)
        if a == b:
            continue
        change = "added" if a is None else "removed" if b is None else "changed"
        row = {"file": path, "change": change, "name": label, "old": a, "new": b}
        if (b or "").endswith("-SNAPSHOT"):
            row["snapshot"] = True
        if bc.PLATFORM_VERSION_RE.search(label):
            row["platform"] = True
        rows.append(row)
    return rows


# ---------------------------------------------------------------- migrations

LIQUIBASE_CHANGES = (
    "createTable", "dropTable", "addColumn", "dropColumn", "modifyDataType", "renameColumn", "renameTable",
    "createIndex", "dropIndex", "addForeignKeyConstraint", "dropForeignKeyConstraint", "addUniqueConstraint",
    "dropUniqueConstraint", "addNotNullConstraint", "dropNotNullConstraint", "addDefaultValue",
    "dropDefaultValue", "addPrimaryKey", "dropPrimaryKey", "update", "delete", "insert", "loadData",
    "loadUpdateData", "customChange", "sql", "sqlFile", "createView", "dropView", "createSequence",
    "createProcedure", "mergeColumns", "addAutoIncrement",
)
LB_OP = {"createTable": "create_table", "dropTable": "drop_table", "addColumn": "add_column",
         "dropColumn": "drop_column", "modifyDataType": "modify_column", "renameColumn": "rename_column",
         "renameTable": "rename_table", "createIndex": "create_index", "dropIndex": "drop_index",
         "addForeignKeyConstraint": "add_constraint", "dropForeignKeyConstraint": "drop_constraint",
         "addUniqueConstraint": "add_constraint", "dropUniqueConstraint": "drop_constraint",
         "addNotNullConstraint": "alter_column", "dropNotNullConstraint": "alter_column",
         "addDefaultValue": "alter_column", "dropDefaultValue": "alter_column", "addPrimaryKey": "add_constraint",
         "dropPrimaryKey": "drop_constraint", "update": "update_data", "delete": "delete_data",
         "insert": "insert_data", "loadData": "insert_data", "loadUpdateData": "insert_data",
         "customChange": "custom_change", "sqlFile": "sql_file", "createView": "create_view",
         "dropView": "drop_view", "createSequence": "create_sequence", "createProcedure": "create_procedure",
         "mergeColumns": "merge_columns", "addAutoIncrement": "alter_column"}
CONSTRAINT_WORDS = r"(?:constraint|index|key|unique|primary|foreign|fulltext|spatial|check)\b"


def _name(raw):
    return (raw or "").strip().strip("`\"[]").lower() or None


def _attrs(text):
    return dict(re.findall(r'([\w:-]+)\s*=\s*"([^"]*)"', text))


def sql_ops(text):
    """Operations of plain SQL: tables and columns created, changed, dropped; data changed."""
    text = re.sub(r"/\*.*?\*/", " ", text, flags=re.S)
    text = re.sub(r"--[^\n]*", " ", text)
    text = re.sub(r"'(?:[^'\\]|\\.|'')*'", "''", text)
    ops = []
    for stmt in text.split(";"):
        s = squash_value(stmt)
        if not s:
            continue
        low = s.lower()
        m = re.match(r"create\s+table\s+(?:if\s+not\s+exists\s+)?([`\"\w.]+)\s+like\s+([`\"\w.]+)", s, re.I)
        if m:
            ops.append({"op": "create_table", "table": _name(m.group(1)), "columns": [],
                        "detail": f"LIKE {_name(m.group(2))}"})
            continue
        if re.match(r"(set\s+@|prepare\b|execute\b|deallocate\b)", low):
            if low.startswith(("set", "prepare")):
                ops.append({"op": "dynamic_sql", "table": None, "detail": s[:80]})
            continue
        m = re.match(r"create\s+(?:or\s+replace\s+)?table\s+(?:if\s+not\s+exists\s+)?([`\"\w.]+)\s*\((.*)\)", s, re.I | re.S)
        if m:
            cols = []
            for part in ji.split_top(m.group(2)):
                w = part.split()
                if w and not re.match(CONSTRAINT_WORDS, part, re.I):
                    cols.append(_name(w[0]))
            ops.append({"op": "create_table", "table": _name(m.group(1)), "columns": cols})
            continue
        m = re.match(r"alter\s+table\s+(?:if\s+exists\s+)?(?:only\s+)?([`\"\w.]+)\s+(.*)$", s, re.I | re.S)
        if m:
            table = _name(m.group(1))
            for clause in ji.split_top(m.group(2)):
                ops.append(_alter_clause(table, clause))
            continue
        for rx, op in (
            (r"drop\s+table\s+(?:if\s+exists\s+)?([`\"\w.]+)", "drop_table"),
            (r"truncate\s+(?:table\s+)?([`\"\w.]+)", "truncate"),
            (r"update\s+([`\"\w.]+)\s+set\b", "update_data"),
            (r"delete\s+from\s+([`\"\w.]+)", "delete_data"),
            (r"insert\s+(?:ignore\s+)?into\s+([`\"\w.]+)", "insert_data"),
            (r"rename\s+table\s+([`\"\w.]+)\s+to\s+([`\"\w.]+)", "rename_table"),
        ):
            m = re.match(rx, s, re.I)
            if m:
                row = {"op": op, "table": _name(m.group(1))}
                if op == "rename_table":
                    row["detail"] = f"→ {_name(m.group(2))}"
                if op in ("update_data", "delete_data") and not re.search(r"\bwhere\b", low):
                    row["detail"] = "không có WHERE"
                ops.append(row)
                break
        else:
            m = re.match(r"create\s+(unique\s+)?index\s+(?:if\s+not\s+exists\s+)?([`\"\w.]+)\s+on\s+([`\"\w.]+)\s*\((.*?)\)", s, re.I)
            if m:
                ops.append({"op": "create_index", "table": _name(m.group(3)),
                            "detail": f"{'unique ' if m.group(1) else ''}{_name(m.group(2))} ({squash_value(m.group(4))})"})
                continue
            m = re.match(r"drop\s+index\s+(?:if\s+exists\s+)?([`\"\w.]+)(?:\s+on\s+([`\"\w.]+))?", s, re.I)
            if m:
                ops.append({"op": "drop_index", "table": _name(m.group(2)), "detail": _name(m.group(1))})
                continue
            m = re.match(r"(create|drop)\s+(?:or\s+replace\s+)?(view|sequence|trigger|procedure|function)\s+"
                         r"(?:if\s+(?:not\s+)?exists\s+)?([`\"\w.]+)", s, re.I)
            if m:
                ops.append({"op": f"{m.group(1).lower()}_{m.group(2).lower()}", "table": _name(m.group(3))})
    return ops


def _alter_clause(table, clause):
    c = squash_value(clause)
    rules = (
        (rf"add\s+(?:column\s+)?(?:if\s+not\s+exists\s+)?(?!{CONSTRAINT_WORDS})([`\"\w]+)\s*(.*)$", "add_column"),
        (rf"add\s+({CONSTRAINT_WORDS}.*)$", "add_constraint"),
        (rf"drop\s+(?:column\s+)?(?:if\s+exists\s+)?(?!{CONSTRAINT_WORDS})([`\"\w]+)\s*(.*)$", "drop_column"),
        (rf"drop\s+({CONSTRAINT_WORDS}.*)$", "drop_constraint"),
        (r"modify\s+(?:column\s+)?([`\"\w]+)\s*(.*)$", "modify_column"),
        (r"change\s+(?:column\s+)?([`\"\w]+)\s+([`\"\w]+)\s*(.*)$", "change_column"),
        (r"rename\s+column\s+([`\"\w]+)\s+to\s+([`\"\w]+)", "rename_column"),
        (r"rename\s+(?:to|as)\s+([`\"\w.]+)", "rename_table"),
        (r"alter\s+(?:column\s+)?([`\"\w]+)\s*(.*)$", "alter_column"),
    )
    for rx, op in rules:
        m = re.match(rx, c, re.I)
        if not m:
            continue
        if op in ("add_constraint", "drop_constraint"):
            return {"op": op, "table": table, "detail": m.group(1)[:80]}
        if op == "rename_table":
            return {"op": op, "table": table, "detail": f"→ {_name(m.group(1))}"}
        if op in ("change_column", "rename_column"):
            row = {"op": op, "table": table, "column": _name(m.group(1)), "new_column": _name(m.group(2))}
            if op == "change_column":
                row["detail"] = m.group(3)[:80]
            return row
        return {"op": op, "table": table, "column": _name(m.group(1)), "detail": (m.group(2) or "")[:80]}
    return {"op": "alter_other", "table": table, "detail": c[:80]}


def liquibase_xml_ops(text):
    ops, changesets = [], []
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    for cs in re.finditer(r"<changeSet\b([^>]*)>(.*?)</changeSet>", text, re.S):
        a = _attrs(cs.group(1))
        # The rollback block holds the reverse operations; they do not run on update.
        body, rollbacks = re.subn(r"<rollback\b[^>]*?(?:/>|>.*?</rollback>)", "", cs.group(2), flags=re.S)
        cid = a.get("id")
        changesets.append({"id": cid, "author": a.get("author"),
                           "context": a.get("context") or a.get("contexts"), "labels": a.get("labels"),
                           "runOnChange": a.get("runOnChange"), "runAlways": a.get("runAlways"),
                           "preconditions": "<preConditions" in body, "rollback": bool(rollbacks)})
        names = "|".join(LIQUIBASE_CHANGES)
        for tm in re.finditer(rf"<({names})\b([^>]*?)(/?)>", body):
            kind, attrs = tm.group(1), _attrs(tm.group(2))
            inner = ""
            if not tm.group(3):
                close = body.find(f"</{kind}>", tm.end())
                inner = body[tm.end():close] if close >= 0 else ""
            ops.extend(_lb_change(kind, attrs, inner, cid))
    return ops, changesets


def _lb_change(kind, a, inner, cid):
    table = _name(a.get("tableName") or a.get("baseTableName") or a.get("oldTableName") or a.get("viewName")
                  or a.get("sequenceName"))
    base = {"changeset": cid}
    if kind == "sql":
        return [{**op, **base} for op in sql_ops(re.sub(r"<!\[CDATA\[|\]\]>", "", inner))] \
            or [{"op": "sql", "table": None, **base}]
    if kind in ("addColumn", "createTable"):
        cols = [_name(x) for x in re.findall(r'<column\b[^>]*\bname\s*=\s*"([^"]+)"', inner)]
        if kind == "createTable":
            return [{"op": "create_table", "table": table, "columns": cols, **base}]
        return [{"op": "add_column", "table": table, "column": c, **base} for c in cols] \
            or [{"op": "add_column", "table": table, **base}]
    if kind == "dropColumn":
        cols = [a.get("columnName")] if a.get("columnName") else re.findall(r'<column\b[^>]*\bname\s*=\s*"([^"]+)"', inner)
        return [{"op": "drop_column", "table": table, "column": _name(c), **base} for c in cols] \
            or [{"op": "drop_column", "table": table, **base}]
    row = {"op": LB_OP.get(kind, kind), "table": table, **base}
    if a.get("columnName"):
        row["column"] = _name(a["columnName"])
    if kind == "renameColumn":
        row["column"], row["new_column"] = _name(a.get("oldColumnName")), _name(a.get("newColumnName"))
    elif kind == "renameTable":
        row["detail"] = f"→ {_name(a.get('newTableName'))}"
    elif kind == "modifyDataType":
        row["detail"] = a.get("newDataType")
    elif kind in ("createIndex", "dropIndex"):
        row["detail"] = a.get("indexName")
    elif kind in ("addNotNullConstraint", "dropNotNullConstraint", "addDefaultValue", "dropDefaultValue"):
        row["detail"] = kind
    elif kind == "customChange":
        row["detail"] = a.get("class")
    elif kind == "sqlFile":
        row["detail"] = a.get("path")
    elif kind in ("update", "delete") and "<where" not in inner:
        row["detail"] = "không có WHERE"
    return [row]


def liquibase_text_ops(text):
    """YAML or JSON changelogs: a rough read of change types and their table/column names."""
    names = "|".join(LIQUIBASE_CHANGES)
    ops, changesets = [], []
    for m in re.finditer(r"""["']?changeSet["']?\s*:(.*?)(?=["']?changeSet["']?\s*:|\Z)""", text, re.S):
        body = m.group(1)
        cut = re.search(r"""["']?rollback["']?\s*:""", body)
        has_rollback = cut is not None
        if cut:
            body = body[:cut.start()]
        get = lambda key, src=body: (re.search(rf"""["']?{key}["']?\s*:\s*["']?([^"'\s,}}]+)""", src) or [None, None])[1]  # noqa: E731
        cid = get("id")
        changesets.append({"id": cid, "author": get("author"), "context": get("context") or get("contexts"),
                           "labels": get("labels"), "runOnChange": get("runOnChange"),
                           "runAlways": get("runAlways"), "preconditions": "preConditions" in body,
                           "rollback": has_rollback})
        marks = list(re.finditer(rf"""["']?({names})["']?\s*:""", body))
        for i, cm in enumerate(marks):
            seg = body[cm.end():marks[i + 1].start() if i + 1 < len(marks) else len(body)]
            a = {k: get(k, seg) for k in ("tableName", "baseTableName", "columnName", "oldColumnName",
                                          "newColumnName", "newTableName", "newDataType", "indexName", "class",
                                          "path") if get(k, seg)}
            if cm.group(1) == "sql":
                # "- sql:" (the change) and its "sql: |" attribute both match: each segment is read as SQL,
                # the change's own segment only holds attributes such as splitStatements and yields nothing.
                text = re.sub(r"""^\s*[|>][-+]?""", "", seg)
                ops.extend({**op, "changeset": cid} for op in sql_ops(text.strip().strip("\"',")))
                continue
            inner = "".join(f'<column name="{c}"/>' for c in re.findall(r"""["']?name["']?\s*:\s*["']?(\w+)""", seg))
            ops.extend(_lb_change(cm.group(1), a, inner, cid))
    return ops, changesets


FORMATTED_SQL_RE = re.compile(r"^\s*--\s*changeset\s+([^:\s]+):(\S+)(.*)$", re.I | re.M)


def migration_ops(path, text):
    """-> (ops, changesets) for one migration file."""
    if not text:
        return [], []
    low = path.lower()
    if low.endswith(".java"):
        return [{"op": "java_migration", "table": None, "detail": os.path.basename(path)}], []
    if low.endswith(".xml"):
        return liquibase_xml_ops(text)
    if low.endswith((".yaml", ".yml", ".json")):
        return liquibase_text_ops(text)
    if FORMATTED_SQL_RE.search(text):
        ops, changesets = [], []
        parts = FORMATTED_SQL_RE.split(text)
        for i in range(1, len(parts) - 3, 4):
            author, cid, rest, body = parts[i], parts[i + 1], parts[i + 2], parts[i + 3]
            ctx = re.search(r"\bcontext(?:s)?:(\S+)", rest)
            changesets.append({"id": cid, "author": author, "context": ctx.group(1) if ctx else None,
                               "labels": None, "runOnChange": "runOnChange:true" in rest,
                               "runAlways": "runAlways:true" in rest, "preconditions": "--precondition" in body,
                               "rollback": "--rollback" in body})
            ops.extend({**op, "changeset": cid} for op in sql_ops(body))
        return ops, changesets
    return sql_ops(text), []


# ---------------------------------------------------------------- java inventories

def java_inventory(repo, rev, paths):
    inv = {"endpoints": [], "data": {}, "entities": {}, "enums": {}, "scheduled": {}, "listeners": {},
           "files": {}}
    for path, text in read_files(repo, rev, paths).items():
        one = ji.inventory(text, path)
        inv["endpoints"].extend(one["endpoints"])
        for key in ("data", "entities", "enums", "scheduled", "listeners"):
            inv[key].update(one[key])
        for key in ("enums", "scheduled", "listeners"):
            for name in one[key]:
                inv["files"][name] = path
    return inv


def diff_maps(old, new, files):
    rows = []
    for key in sorted(set(old) | set(new)):
        a, b = old.get(key), new.get(key)
        if a == b:
            continue
        rows.append({"name": key, "change": "added" if a is None else "removed" if b is None else "changed",
                     "old": a, "new": b, "file": files.get(key)})
    return rows


def diff_entities(old, new):
    rows = []
    for name in sorted(set(old) | set(new)):
        a, b = old.get(name), new.get(name)
        if a == b:
            continue
        if a is None or b is None:
            e = b or a
            rows.append({"entity": name, "change": "added" if a is None else "removed", "table": e["table"],
                         "file": e["file"], "columns": sorted(e["columns"])})
            continue
        ca, cb = a["columns"], b["columns"]
        rows.append({
            "entity": name, "change": "changed", "table": b["table"], "file": b["file"],
            "old_table": a["table"] if a["table"] != b["table"] else None,
            "added": [{"column": c, **cb[c]} for c in sorted(set(cb) - set(ca))],
            "removed": [{"column": c, **ca[c]} for c in sorted(set(ca) - set(cb))],
            "changed": [{"column": c, "old": ca[c], "new": cb[c]} for c in sorted(set(ca) & set(cb)) if ca[c] != cb[c]],
        })
    return rows


WIRE_PATH_RE = re.compile(r"(^|/)(web|rest|api|external|client|clients|feign|messages?|events?|kafka|payloads?|"
                          r"contracts?)/", re.I)


def exposure(data_all, endpoint_list):
    """class name -> how it reaches the outside: an endpoint or Feign call using it, a field of such a
    class, or a wire-looking package (web, external, messages, ...). Classes not listed are internal."""
    by_simple = {}
    for name in data_all:
        by_simple.setdefault(name.split(".")[-1], []).append(name)
    out = {}
    for e in endpoint_list:
        label = f"{e['method']} {e['path']}" + (f" ({e['client']})" if e["client"] else "")
        for word in re.findall(r"\w+", f"{e['body'] or ''} {e['returns'] or ''}"):
            for name in by_simple.get(word, []):
                out.setdefault(name, set()).add(label)
    for name, d in data_all.items():
        if WIRE_PATH_RE.search(d["file"]) or bc.MESSAGE_PATH_RE.search(d["file"]):
            out.setdefault(name, set()).add("gói " + os.path.dirname(d["file"]).rsplit("/", 1)[-1])
    queue = list(out)
    while queue:
        parent = queue.pop()
        fields = data_all.get(parent, {}).get("fields", {})
        children = set()
        for f in fields.values():
            for word in re.findall(r"\w+", f["type"]):
                children.update(by_simple.get(word, []))
        children.update(n for n in data_all if n.startswith(parent + "."))
        for child in children:
            if child not in out:
                out[child] = {f"qua {parent.split('.')[-1]}"}
                queue.append(child)
    return {k: sorted(v) for k, v in out.items()}


def diff_data(old, new, endpoint_list):
    exposed = exposure({**old, **new}, endpoint_list)
    rows = []
    for name in sorted(set(old) | set(new)):
        a, b = old.get(name), new.get(name)
        if a == b:
            continue
        row = {"class": name, "file": (b or a)["file"], "used_by": exposed.get(name, []),
               "exposed": name in exposed}
        if a is None or b is None:
            row.update(change="added" if a is None else "removed", fields=sorted((b or a)["fields"]))
        else:
            added, removed, changed = ji.diff_fields(a["fields"], b["fields"])
            breaking = [f"thêm field bắt buộc {f['field']}" for f in added if f["required"]]
            breaking += [f"bỏ field {f['field']}" for f in removed]
            for f in changed:
                if "type" in f["what"]:
                    breaking.append(f"đổi kiểu {f['field']}: {f['old']['type']} → {f['new']['type']}")
                if "json" in f["what"]:
                    breaking.append(f"đổi tên JSON {f['field']}")
                if "validation" in f["what"] and f["new"]["required"] and not f["old"]["required"]:
                    breaking.append(f"{f['field']} thành bắt buộc")
            row.update(change="changed", added=added, removed=removed, changed=changed,
                       breaking=breaking if row["exposed"] else [],
                       internal_changes=[] if row["exposed"] else breaking)
        rows.append(row)
    return rows


def consistency(entities_now, entity_rows, enum_rows, ops, has_migrations, known=None):
    """Entity/migration mismatches and persisted enum changes. known(name) -> True when an older migration
    (outside the range) already mentions that table or column."""
    known = known or (lambda name: False)
    out = []
    covered_cols, covered_tables = set(), set()
    for op in ops:
        t = op.get("table")
        if op["op"] == "create_table":
            covered_tables.add(t)
            covered_cols.update((t, c) for c in op.get("columns") or [])
        for c in (op.get("column"), op.get("new_column")):
            if c:
                covered_cols.add((t, c))
    if has_migrations:
        for row in entity_rows:
            if row["change"] == "added" and row["table"].lower() not in covered_tables \
                    and not known(row["table"]):
                out.append(ps.finding("medium", "entity-table-without-migration", row["file"], None,
                                      f"entity mới {row['entity']} (bảng `{row['table']}`) nhưng không thấy migration "
                                      "tạo bảng trong dải này: kiểm tra ddl-auto hoặc migration ở nơi khác"))
            for col in row.get("added", []):
                if (row["table"].lower(), col["column"]) not in covered_cols \
                        and not col["column"].startswith("<embedded") and not known(col["column"]):
                    out.append(ps.finding("medium", "entity-column-without-migration", row["file"], None,
                                          f"{row['entity']}.{col['field']} → `{row['table']}.{col['column']}` mới "
                                          "nhưng không thấy migration thêm cột trong dải này"))
    mapped = {}
    for name, e in entities_now.items():
        for col, info in e["columns"].items():
            mapped[(e["table"].lower(), col)] = (name, info, e["file"])
    tables_now = {e["table"].lower(): name for name, e in entities_now.items()}
    for op in ops:
        t, c = op.get("table"), op.get("column")
        if op["op"] in ("drop_column", "rename_column", "change_column") and (t, c) in mapped \
                and not (op["op"] == "change_column" and op.get("new_column") == c):
            name, info, file = mapped[(t, c)]
            out.append(ps.finding("high", "migration-drops-mapped-column", file, None,
                                  f"migration {op['op']} `{t}.{c}` nhưng entity {name}.{info['field']} vẫn map cột này"))
        if op["op"] in ("drop_table", "rename_table") and t in tables_now:
            out.append(ps.finding("high", "migration-drops-mapped-table", "-", None,
                                  f"migration {op['op']} `{t}` nhưng entity {tables_now[t]} vẫn map bảng này"))
    persisted = {}
    for name, e in entities_now.items():
        for info in e["columns"].values():
            enum = next((x for x in info["attributes"] if x.startswith("enum=")), None)
            if enum:
                persisted.setdefault(info["type"].split(".")[-1], set()).add((name, enum.split("=")[1]))
    for row in enum_rows:
        if row["change"] != "changed":
            continue
        simple = row["name"].split(".")[-1]
        uses = persisted.get(simple)
        if not uses:
            continue
        old, new = row["old"], row["new"]
        gone = [x for x in old if x not in new]
        if gone:
            out.append(ps.finding("high", "enum-persisted-constant-removed", row.get("file") or "-", None,
                                  f"enum {simple} bỏ {', '.join(gone)} nhưng được lưu DB qua "
                                  f"{', '.join(sorted(n for n, _ in uses))}: dòng cũ mang giá trị này sẽ lỗi khi đọc"))
        if any(kind == "ORDINAL" for _, kind in uses) and new[:len(old)] != old:
            out.append(ps.finding("high", "enum-ordinal-reordered", row.get("file") or "-", None,
                                  f"enum {simple} lưu theo ORDINAL mà thứ tự hằng đổi: dữ liệu cũ đọc ra sai giá trị"))
    return out


# ---------------------------------------------------------------- assemble

def changes(repo, frm="auto", to="HEAD", dialect_flag="auto", target_envs=bc.DEFAULT_TARGET_ENVS, today=None):
    bc.check_repo(repo)
    to_ref, to_sha, to_kind = resolve_ref(repo, to)
    if not frm or frm == "auto":
        tag = previous_tag(repo, to_sha)
        if not tag:
            raise ps.ScanError(f"không tìm được tag trước {to_ref}; nêu --from")
        frm = tag
    from_ref, from_sha, from_kind = resolve_ref(repo, frm)
    if from_sha == to_sha:
        raise ps.ScanError(f"{from_ref} và {to_ref} cùng commit {to_sha[:8]}: không có thay đổi")
    merge_base = run(repo, ["merge-base", from_sha, to_sha], check=False).strip()
    if not merge_base:
        raise ps.ScanError("hai ref không có commit chung")
    ancestor = merge_base == from_sha
    dialect, dialect_source = ps.resolve_dialect(repo, to_sha, dialect_flag)
    ctx = {"dialect": dialect}
    target_envs = tuple(e.lower() for e in target_envs)

    change = bc.analyze_change(repo, from_sha, to_sha, from_sha, merge_base, to_sha, [], ctx, target_envs)
    files = change["files"]
    status = {f["path"]: f["status"] for f in files}
    net_paths = set(status)
    commits = commits_between(repo, from_sha, to_sha, ctx, target_envs, net_paths)

    # Java: controllers/clients and entities in full at both revisions, other classes where they changed.
    java_changed = [p for p in status if p.endswith(".java") and not ps.TEST_RE.search(p)]
    api_a = grep_files(repo, from_sha, JAVA_HINT_RE)
    api_b = grep_files(repo, to_sha, JAVA_HINT_RE)
    ent_a = grep_files(repo, from_sha, r"@Entity\b")
    ent_b = grep_files(repo, to_sha, r"@Entity\b")
    inv_a = java_inventory(repo, from_sha, set(api_a) | set(ent_a) | {p for p in java_changed if status[p] != "A"})
    inv_b = java_inventory(repo, to_sha, set(api_b) | set(ent_b) | {p for p in java_changed if status[p] != "D"})
    changed_set = set(java_changed)

    def only_changed(mapping):
        return {k: v for k, v in mapping.items() if v["file"] in changed_set}

    endpoints = ji.diff_endpoints(inv_a["endpoints"], inv_b["endpoints"])
    data_rows = diff_data(only_changed(inv_a["data"]), only_changed(inv_b["data"]),
                          inv_a["endpoints"] + inv_b["endpoints"])
    entity_rows = diff_entities(inv_a["entities"], inv_b["entities"])
    files_of = {**inv_a["files"], **inv_b["files"]}
    enum_rows = diff_maps(inv_a["enums"], inv_b["enums"], files_of)
    sched_rows = diff_maps(inv_a["scheduled"], inv_b["scheduled"], files_of)
    listener_rows = diff_maps(inv_a["listeners"], inv_b["listeners"], files_of)

    # Config: values before and after in packaged config files.
    cfg_paths = [p for p in status if CONFIG_FILE_RE.search(p) and not ps.TEST_RE.search(p)]
    old_cfg = read_files(repo, from_sha, cfg_paths)
    new_cfg = read_files(repo, to_sha, cfg_paths)
    config_rows = []
    for p in cfg_paths:
        config_rows.extend(diff_config(p, old_cfg.get(p), new_cfg.get(p)))
    topics_now = {}
    all_cfg = [x for x in run(repo, ["ls-tree", "-r", "--name-only", to_sha], check=False).splitlines()
               if CONFIG_FILE_RE.search(x) and not ps.TEST_RE.search(x)]
    for p, text in read_files(repo, to_sha, all_cfg).items():
        for k, v in config_values(p, text).items():
            if "topic" in k.lower() and v:
                topics_now.setdefault(k, mask_value(k, v))
    other_config = [{"path": f["path"], "status": f["status"], "categories": f["categories"],
                     "keys": [c for c in change["config_changes"] if c["file"] == f["path"]]}
                    for f in files if set(f["categories"]) & {"config", "deploy"} and f["path"] not in cfg_paths
                    and "test" not in f["categories"]]

    # Dependencies.
    build_paths = [p for p in status if ps.BUILD_RE.search(p) and re.search(r"(pom\.xml|\.gradle(\.kts)?|\.toml)$", p)]
    old_b, new_b = read_files(repo, from_sha, build_paths), read_files(repo, to_sha, build_paths)
    dep_rows = []
    for p in build_paths:
        dep_rows.extend(diff_build(p, old_b.get(p), new_b.get(p)))

    # Migrations: operations of new files in full, of changed files on their added lines.
    patch_added = {}
    mig_files = [m["file"] for m in change["migrations"] if not m["changelog_index"]]
    if any(status[p] == "M" for p in mig_files):
        for pf in ps.parse_patch(run(repo, ["diff", "--no-renames", "-U0", from_sha, to_sha, "--",
                                           *[p for p in mig_files if status[p] == "M"]])):
            patch_added[pf["path"]] = "\n".join(t for _, t in pf["added"])
    mig_text = read_files(repo, to_sha, [p for p in mig_files if status[p] == "A"])
    all_ops, migrations = [], []
    for m in change["migrations"]:
        p = m["file"]
        row = {"file": p, "status": m["status"], "changelog_index": m["changelog_index"],
               "statements": m["statements"], "ops": [], "changesets": []}
        if not m["changelog_index"] and m["status"] in ("A", "M"):
            text = mig_text.get(p) if m["status"] == "A" else patch_added.get(p, "")
            row["ops"], row["changesets"] = migration_ops(p, text)
            if m["status"] == "M":
                row["partial"] = True
            all_ops.extend(row["ops"])
        migrations.append(row)
    # MongoDB: Mongock change units are Java classes, not files pr_scan knows as migrations.
    mongo_paths = [p for p in java_changed if status[p] in ("A", "M")]
    for p, text in read_files(repo, to_sha, mongo_paths).items():
        if not MONGOCK_RE.search(text):
            continue
        ops = sorted({m.group(1) or f"@{m.group(2)}" for m in MONGO_INDEX_RE.finditer(text)})
        row = {"file": p, "status": status[p], "changelog_index": False, "statements": {}, "kind": "mongock",
               "ops": [{"op": "mongock_change", "table": None, "detail": ", ".join(ops) or None}], "changesets": []}
        migrations.append(row)
        mig_files.append(p)
    has_migrations = bool(mig_files) or any(
        "migration" in ps.categorize(p) for p in run(repo, ["ls-tree", "-r", "--name-only", to_sha],
                                                     check=False).splitlines())
    tree_b = run(repo, ["ls-tree", "-r", "--name-only", to_sha], check=False).splitlines()
    all_migrations = [p for p in tree_b if "migration" in ps.categorize(p) and not ps.TEST_RE.search(p)]

    def known(name):
        """The column or table already appears in a migration of the newer revision (e.g. created long ago)."""
        if not all_migrations or not re.fullmatch(r"[\w.]+", name or ""):
            return False
        return bool(run(repo, ["grep", "-l", "-i", "-w", "-F", name, to_sha, "--", *all_migrations], check=False).strip())

    findings = change["findings"] + consistency(inv_b["entities"], entity_rows, enum_rows, all_ops, has_migrations,
                                                known)
    findings.sort(key=lambda x: (ps.SEVERITY_ORDER[x["severity"]], x["file"], x["line"] or 0))

    # Logic files: source code that is not only a data class, for the reader to open first.
    data_files = {v["file"] for v in list(inv_b["data"].values()) + list(inv_b["entities"].values())}
    logic = []
    for f in files:
        if "source" not in f["categories"] or f["path"] in data_files:
            continue
        logic.append({"path": f["path"], "status": f["status"], "categories": f["categories"],
                      "added": f["added"], "removed": f["removed"]})
    logic.sort(key=lambda x: -(x["added"] + x["removed"]))
    areas = {}
    for d in commits["new"]:
        for a in d.get("areas", []):
            areas.setdefault(a, []).append(d["short"])

    breaking = sum(1 for e in endpoints if e["change"] == "removed" or e.get("breaking")) \
        + sum(1 for d in data_rows if d["exposed"] and (d["change"] == "removed" or d.get("breaking")))
    category_counts = Counter(c for f in files for c in f["categories"])
    today = today or datetime.date.today().strftime("%Y%m%d")
    name = f"changes-{ps.slug(from_ref)}-to-{ps.slug(to_ref)}-{ps.slug(bc.repo_name(repo))}-{today}.md"
    new = commits["new"]
    return {
        "range": {
            "repo": bc.repo_name(repo), "repo_path": os.path.abspath(repo),
            "from": frm, "from_ref": from_ref, "from_sha": from_sha, "from_kind": from_kind,
            "from_date": bc.commit_date(repo, from_sha),
            "to": to, "to_ref": to_ref, "to_sha": to_sha, "to_kind": to_kind, "to_date": bc.commit_date(repo, to_sha),
            "merge_base": merge_base, "from_is_ancestor": ancestor,
            "dialect": dialect, "dialect_source": dialect_source,
            "files": len(files), "added": sum(f["added"] for f in files), "removed": sum(f["removed"] for f in files),
            "categories": dict(category_counts),
            "counts": {"commits": len(new), "duplicates": len(commits["duplicates"]),
                       "equivalent": len(commits["equivalent"]), "reverted_pairs": len(commits["reverted"]),
                       "no_net_effect": len(commits["no_net_effect"]), "only_in_from": len(commits["only_in_from"]),
                       "merges_skipped": commits["merges_skipped"],
                       "endpoints": len(endpoints), "data_classes": len(data_rows), "entities": len(entity_rows),
                       "config_keys": len(config_rows), "migrations": len(migrations), "dependencies": len(dep_rows),
                       "breaking": breaking,
                       "findings_high": sum(1 for x in findings if x["severity"] == "high")},
            "authors": sorted({d["author"] for d in new}),
            "first_date": min((d["date"] for d in new), default=None),
            "last_date": max((d["date"] for d in new), default=None),
            "report_name": name,
        },
        "commits": commits,
        "tickets": tickets(new),
        "areas": areas,
        "api": {"endpoints": endpoints, "data_classes": data_rows,
                "feign_now": [{k: e[k] for k in ("client", "method", "path", "handler")}
                              for e in inv_b["endpoints"] if e["kind"] == "feign"]},
        "kafka": {"topics_now": topics_now},
        "business": {"enums": enum_rows, "scheduled": sched_rows, "kafka_listeners": listener_rows,
                     "logic_files": logic[:60]},
        "config": {"packaged": config_rows, "required_keys": change["required_keys"], "other_files": other_config,
                   "snippet": config_snippet(config_rows, change["required_keys"],
                                             {p: [k.split(" @")[0] for k in config_values(p, new_cfg.get(p))]
                                              for p in cfg_paths})},
        "database": {"migrations": migrations, "entities": entity_rows},
        "dependencies": dep_rows,
        "findings": findings,
        "files": files,
    }


# ---------------------------------------------------------------- discover

def discover(parent, frm, to):
    """Repositories under parent where both refs resolve and differ."""
    rows = []
    for entry in sorted(os.listdir(parent)):
        path = os.path.join(parent, entry)
        if not os.path.isdir(path) or not os.path.exists(os.path.join(path, ".git")):
            continue
        row = {"repo": entry, "path": path}
        try:
            to_ref, to_sha, _ = resolve_ref(path, to)
            f = frm if frm and frm != "auto" else previous_tag(path, to_sha)
            if not f:
                raise ps.ScanError("không có tag trước")
            from_ref, from_sha, _ = resolve_ref(path, f)
        except ps.ScanError as exc:
            rows.append({**row, "status": "skip", "reason": str(exc)})
            continue
        count = int(run(path, ["rev-list", "--no-merges", "--count", f"{from_sha}..{to_sha}"], check=False).strip() or 0)
        rows.append({**row, "status": "same" if from_sha == to_sha else "ok", "from_ref": from_ref,
                     "from_sha": from_sha, "to_ref": to_ref, "to_sha": to_sha, "commits": count})
    return rows


# ---------------------------------------------------------------- across services

def path_tail(path):
    """/${ctx}/api/v1/x/{id} -> /api/v1/x/{}: placeholders in front (context paths) dropped."""
    return ji.norm_path(re.sub(r"^(/\$\{[^}]*\})+", "", path) or "/")


def cross(reports):
    """Links between services of one run: a changed endpoint another service calls through Feign, a topic
    changed in one service and configured in another."""
    rows = []
    for x in reports:
        rx = x["range"]["repo"]
        for e in x["api"]["endpoints"]:
            if e["kind"] != "http" or e["change"] == "added":
                continue
            tail = path_tail(e["path"])
            for y in reports:
                if y is x:
                    continue
                for f in y["api"].get("feign_now", []):
                    ft = path_tail(f["path"])
                    if f["method"] in (e["method"], "ANY") and (ft == tail or ft.endswith(tail) or tail.endswith(ft)) \
                            and ft.count("/") >= 2:
                        rows.append({"kind": "api", "provider": rx, "consumer": y["range"]["repo"],
                                     "what": f"{e['method']} {tail}", "change": e["change"],
                                     "detail": "; ".join(e.get("breaking") or []) or (
                                         "đổi " + ", ".join(d["field"] for d in e.get("differences", []))
                                         if e["change"] == "changed" else "bị bỏ"),
                                     "consumer_side": f"{f['client']} · {f['handler']}"})
    # One row per topic: the services whose config changed it, and every other service configuring it.
    topics = {}
    for x in reports:
        rx = x["range"]["repo"]
        for c in x["config"]["packaged"]:
            if "topic" not in c["key"].lower():
                continue
            for v in {v for v in (c["old"], c["new"]) if v}:
                t = topics.setdefault(v, {"changed": {}, "configured": {}})
                t["changed"][rx] = f"{c['key']}: {c['old']} → {c['new']}"
    for v, t in topics.items():
        for y in reports:
            ry = y["range"]["repo"]
            for k, value in y.get("kafka", {}).get("topics_now", {}).items():
                if value == v and ry not in t["changed"]:
                    t["configured"].setdefault(ry, k)
        if len(t["changed"]) + len(t["configured"]) < 2:
            continue
        rows.append({"kind": "topic", "provider": ", ".join(sorted(t["changed"])),
                     "consumer": ", ".join(sorted(t["configured"])) or "(chỉ các service đổi)", "what": v,
                     "change": "; ".join(sorted({d.split(": ", 1)[1] for d in t["changed"].values()})),
                     "detail": "; ".join(f"{r}: {d}" for r, d in sorted(t["changed"].items())),
                     "consumer_side": ", ".join(f"{r}: {k}" for r, k in sorted(t["configured"].items())) or "-"})
    return rows


def render_cross(rows):
    if not rows:
        return "Không thấy liên hệ giữa các service (endpoint đổi có Feign gọi tới, topic dùng chung).\n"
    lines = ["| Loại | Service đổi | Service bị ảnh hưởng | Endpoint / topic | Thay đổi | Phía bị ảnh hưởng |",
             "|---|---|---|---|---|---|"]
    for r in rows:
        lines.append(f"| {r['kind']} | {r['provider']} | {r['consumer']} | `{r['what']}` | {r['detail']} | "
                     f"`{r['consumer_side']}` |")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------- output

def render_text(r):
    g = r["range"]
    n = g["counts"]
    lines = [
        f"Repo: {g['repo']}   {g['from_ref']} ({g['from_kind']}, {g['from_sha'][:8]}, {g['from_date']})  ->  "
        f"{g['to_ref']} ({g['to_kind']}, {g['to_sha'][:8]}, {g['to_date']})"
        + ("" if g["from_is_ancestor"] else f"   [from is not an ancestor of to; merge-base {g['merge_base'][:8]}]"),
        f"Commits: {n['commits']} (+{n['duplicates']} duplicate copies) | equivalent {n['equivalent']} | "
        f"reverted pairs {n['reverted_pairs']} | no net effect {n['no_net_effect']} | only in from {n['only_in_from']} | "
        f"merges skipped {n['merges_skipped']}",
        f"Files: {g['files']} +{g['added']} -{g['removed']}   Database: {g['dialect']} ({g['dialect_source']})",
        f"Endpoints {n['endpoints']} | data classes {n['data_classes']} | entities {n['entities']} | "
        f"config keys {n['config_keys']} | migrations {n['migrations']} | dependencies {n['dependencies']} | "
        f"breaking {n['breaking']} | high findings {n['findings_high']}",
        "", "Commits:",
    ]
    for d in r["commits"]["new"]:
        extra = ("  [" + ", ".join(d["categories"]) + "]") if d.get("categories") else ""
        extra += ("  areas: " + ", ".join(d["areas"])) if d.get("areas") else ""
        lines.append(f"  {d['short']} {d['date']} {d['author']}: {d['subject']}{extra}")
    if r["commits"]["only_in_from"]:
        lines += ["", "Only in from (not in to):"] + [f"  {d['short']} {d['subject']}" for d in r["commits"]["only_in_from"]]
    if r["api"]["endpoints"]:
        lines += ["", "Endpoints:"]
        for e in r["api"]["endpoints"]:
            who = f"[{e['kind']}{':' + e['client'] if e['client'] else ''}]"
            why = ("  BREAKING: " + "; ".join(e["breaking"])) if e.get("breaking") else ""
            diffs = ("  " + ", ".join(d["field"] for d in e.get("differences", []))) if e.get("differences") else ""
            lines.append(f"  {e['change']:<8} {who} {e['method']} {e['path']}  {e['handler']}{diffs}{why}")
    if r["api"]["data_classes"]:
        lines += ["", "Data classes:"]
        for d in r["api"]["data_classes"]:
            detail = ""
            if d["change"] == "changed":
                detail = (f"  +{','.join(f['field'] for f in d['added']) or '-'} "
                          f"-{','.join(f['field'] for f in d['removed']) or '-'} "
                          f"~{','.join(f['field'] for f in d['changed']) or '-'}")
            lines.append(f"  {d['change']:<8} {d['class']}{detail}"
                         + (f"  exposed: {'; '.join(d['used_by'][:3])}" if d["used_by"] else "  (internal)"))
    b = r["business"]
    for title, rows in (("Enums", b["enums"]), ("Scheduled", b["scheduled"]), ("Kafka listeners", b["kafka_listeners"])):
        if rows:
            lines += ["", f"{title}:"] + [f"  {x['change']:<8} {x['name']}: {x['old']} -> {x['new']}" for x in rows]
    if r["config"]["packaged"]:
        lines += ["", "Config (packaged):"]
        lines += [f"  {x['change']:<8} {x['key']}  {x['old']!s} -> {x['new']!s}  ({x['file']})" for x in r["config"]["packaged"]]
    if r["config"]["other_files"]:
        lines += ["", "Other config files:"] + [f"  {x['status']} {x['path']}" for x in r["config"]["other_files"]]
    if r["database"]["migrations"]:
        lines += ["", "Migrations:"]
        for m in r["database"]["migrations"]:
            ops = "; ".join(f"{o['op']} {o.get('table') or ''}{'.' + o['column'] if o.get('column') else ''}".strip()
                            for o in m["ops"][:12])
            lines.append(f"  {m['status']} {m['file']}  {ops or '-'}")
    if r["database"]["entities"]:
        lines += ["", "Entities:"]
        for e in r["database"]["entities"]:
            if e["change"] == "changed":
                lines.append(f"  changed  {e['entity']} ({e['table']}): +{[c['column'] for c in e['added']]} "
                             f"-{[c['column'] for c in e['removed']]} ~{[c['column'] for c in e['changed']]}")
            else:
                lines.append(f"  {e['change']:<8} {e['entity']} ({e['table']})")
    if r["dependencies"]:
        lines += ["", "Dependencies:"] + [f"  {x['change']:<8} {x['name']}: {x['old']} -> {x['new']}" for x in r["dependencies"]]
    lines += ["", f"Findings ({len(r['findings'])}):"]
    lines += [f"  [{x['severity']}] {x['kind']} {ps.where_of(x)} - {x['detail']}" for x in r["findings"]]
    return "\n".join(lines) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description="What changed between two revisions of a service.")
    parser.add_argument("--from", dest="frm", default="auto",
                        help="older tag, branch or commit (default: auto = the tag before --to)")
    parser.add_argument("--to", default="HEAD", help="newer tag, branch or commit (default: HEAD)")
    parser.add_argument("--repo", default=".", help="repository directory (default: current)")
    parser.add_argument("--discover", metavar="DIR",
                        help="list the repositories under DIR where both refs exist, then stop")
    parser.add_argument("--cross", nargs="+", metavar="JSON",
                        help="links between services of one run (several --out files): Markdown table, then stop")
    parser.add_argument("--format", choices=["json", "text"], default="json", help="what to print")
    parser.add_argument("--out", help="also write the JSON report to this file")
    parser.add_argument("--patch-file", help="also write the diff (-U5) to this file")
    parser.add_argument("--dialect", choices=("auto",) + ps.DIALECTS, default="auto",
                        help="database for the SQL rules; auto reads JDBC URLs and drivers in the repository")
    parser.add_argument("--target-envs", default=",".join(bc.DEFAULT_TARGET_ENVS),
                        help="environment names whose config files run with the newer revision")
    args = parser.parse_args(argv)

    if args.cross:
        try:
            reports = []
            for path in args.cross:
                with open(path, encoding="utf-8") as fh:
                    reports.append(json.load(fh))
        except (OSError, ValueError) as exc:
            print(f"ref_changes: không đọc được {path}: {exc}", file=sys.stderr)
            return 2
        rows = cross(reports)
        if args.format == "json":
            json.dump(rows, sys.stdout, ensure_ascii=False, indent=2)
            sys.stdout.write("\n")
        else:
            sys.stdout.write(render_cross(rows))
        return 0

    if args.discover:
        try:
            rows = discover(args.discover, args.frm, args.to)
        except OSError as exc:
            print(f"ref_changes: không đọc được {args.discover}: {exc.strerror or exc}", file=sys.stderr)
            return 2
        if args.format == "text":
            for x in rows:
                if x["status"] == "skip":
                    print(f"skip  {x['repo']}: {x['reason']}")
                else:
                    print(f"{x['status']:<5} {x['repo']}: {x['from_ref']} {x['from_sha'][:8]} -> "
                          f"{x['to_ref']} {x['to_sha'][:8]}  ({x['commits']} commits)")
        else:
            json.dump(rows, sys.stdout, ensure_ascii=False, indent=2)
            sys.stdout.write("\n")
        return 0

    envs = [e.strip() for e in args.target_envs.split(",") if e.strip()]
    written = None
    try:
        report = changes(args.repo, args.frm, args.to, args.dialect, envs)
        g = report["range"]
        for written, content in ((args.patch_file, lambda: run(args.repo, ["diff", "--no-renames", "-U5",
                                                                           g["from_sha"], g["to_sha"]])),
                                 (args.out, lambda: json.dumps(report, ensure_ascii=False, indent=2) + "\n")):
            if written:
                os.makedirs(os.path.dirname(os.path.abspath(written)), exist_ok=True)
                with open(written, "w", encoding="utf-8") as fh:
                    fh.write(content())
    except ps.ScanError as exc:
        print(f"ref_changes: {exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"ref_changes: không ghi được {written}: {exc.strerror or exc}", file=sys.stderr)
        return 2
    if args.format == "text":
        sys.stdout.write(render_text(report))
    else:
        json.dump(report, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
