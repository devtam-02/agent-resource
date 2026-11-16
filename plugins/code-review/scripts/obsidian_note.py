#!/usr/bin/env python3
"""Write change reports into an Obsidian vault.

Settings (~/.dev-cli/.env, or the process environment):
  OBSIDIAN_VAULT                       vault folder (the one holding .obsidian/). Required.
  BRANCH_COMPARE_OBSIDIAN_DIR          folder inside the vault for these reports. Default: code-changes
  BRANCH_COMPARE_OBSIDIAN_SYNC         on/off: run the vault's sync command after writing. Default: on
  BRANCH_COMPARE_OBSIDIAN_SYNC_COMMAND Obsidian command id that syncs the vault. Default: git-vault-sync:sync
  OBSIDIAN_CLI                         Obsidian's command line (Settings > General > CLI). Default: obsidian

Layout: <vault>/<dir>/<repo>/<yyyymmdd-HHMM>-<from>~<to>.md for each service (the repository folder is
created when missing). A run over several services shares one stamp; its summary note is
<vault>/<dir>/_tong-hop/<stamp>-<from>~<to>.md.

Every note gets YAML properties (repo, from, to, counts, tags) so Dataview can query them, and the
marker generated_by: branch-compare (the plugin's former name, kept so that older notes stay recognised). A note without that marker is never overwritten: it was written by
someone else.

  obsidian_note.py check
  obsidian_note.py stamp                                  # prints the run stamp, e.g. 20261115-0142
  obsidian_note.py publish --report "$TMP/svc.md" --json "$TMP/svc.json" --stamp "20261115-0142"
  obsidian_note.py index --stamp "20261115-0142" [--summary "$TMP/summary.md"]   # several services only
  obsidian_note.py sync

sync asks the running Obsidian app, through its CLI, to run the sync command of the vault's sync plugin
(default Git Vault Sync), then waits until the notes are committed in the vault's git repository.

Exit codes: 0 ok, 2 usage or file error, 3 vault not configured or not found, 4 would overwrite a note
this script did not write, 5 Obsidian is not running or its CLI is not installed (nothing synced),
6 sync started but the notes were not committed in time.
"""

import argparse
import datetime
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from plugin_settings import dev_cli_env_path, setting  # noqa: E402

MARKER = "generated_by: branch-compare"
INDEX_DIR = "_tong-hop"
DEFAULT_DIR = "code-changes"
UNSAFE_RE = re.compile(r'[\\/:*?"<>|#^\[\]\x00-\x1f]+')
STAMP_RE = re.compile(r"^\d{8}-(?:[01]\d|2[0-3])[0-5]\d$")
STAMP_FORMAT = "%Y%m%d-%H%M"


class NoteError(Exception):
    def __init__(self, message, code=2):
        super().__init__(message)
        self.code = code


def safe_name(text):
    """A file or folder name Obsidian accepts on every platform."""
    name = UNSAFE_RE.sub("-", text or "").strip(" .-")
    return re.sub(r"\s+", " ", name) or "untitled"


def vault_paths():
    raw, source = setting("OBSIDIAN_VAULT")
    if not raw:
        raise NoteError(f"chưa cấu hình OBSIDIAN_VAULT (thêm dòng OBSIDIAN_VAULT=<thư mục vault> vào "
                        f"{dev_cli_env_path()})", 3)
    vault = os.path.realpath(os.path.expanduser(raw))
    if not os.path.isdir(vault):
        raise NoteError(f"OBSIDIAN_VAULT không phải thư mục: {vault}", 3)
    folder, _ = setting("BRANCH_COMPARE_OBSIDIAN_DIR", DEFAULT_DIR)
    parts = [safe_name(p) for p in re.split(r"[\\/]+", folder) if p.strip() and p.strip() not in (".", "..")]
    base = os.path.join(vault, *parts) if parts else vault
    return vault, base, source


def inside(vault, path):
    real = os.path.realpath(path)
    return real == vault or real.startswith(vault + os.sep)


def yaml_value(value):
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(json.dumps(str(v), ensure_ascii=False) for v in value) + "]"
    if value is None:
        return ""
    return json.dumps(str(value), ensure_ascii=False)


def frontmatter(props):
    lines = ["---"] + [f"{k}: {yaml_value(v)}".rstrip() for k, v in props.items()] + [MARKER, "---", ""]
    return "\n".join(lines)


def strip_frontmatter(text):
    if text.startswith("---\n"):
        end = text.find("\n---", 4)
        if end >= 0:
            nl = text.find("\n", end + 4)
            return text[nl + 1:] if nl >= 0 else ""
    return text


def read_props(path):
    """The YAML properties of a note this script wrote (flat key: value lines only)."""
    try:
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
    except OSError:
        return None
    end = text.find("\n---", 4)
    if not text.startswith("---\n") or end < 0 or MARKER not in text[4:end]:
        return None
    props = {}
    for line in text[4:end].splitlines():
        key, sep, value = line.partition(":")
        if not sep:
            continue
        value = value.strip()
        try:
            props[key.strip()] = json.loads(value) if value else None
        except ValueError:
            props[key.strip()] = value
    return props


def body_hash(text):
    """Hash of a note's body (after the properties), normalised for line endings and trailing space."""
    body = "\n".join(line.rstrip() for line in strip_frontmatter(text).replace("\r\n", "\n").split("\n")).strip()
    return hashlib.sha256(body.encode("utf-8")).hexdigest()[:16]


def write_note(vault, path, text):
    """Write a note this script owns. text is the body; the properties are prepended with its hash, so a later
    run can tell whether someone edited the note in Obsidian (then it is not overwritten)."""
    if not inside(vault, os.path.dirname(path)):
        raise NoteError(f"đường dẫn ra ngoài vault: {path}")
    if os.path.exists(path):
        with open(path, encoding="utf-8", errors="replace") as fh:
            current = fh.read()
        if MARKER not in current[:4096]:
            raise NoteError(f"đã có ghi chú không do plugin này tạo, không ghi đè: {path}", 4)
        stored = (read_props(path) or {}).get("body_hash")
        if stored and stored != body_hash(current):
            raise NoteError(f"ghi chú đã được sửa tay sau lần ghi trước, không ghi đè: {path} (đổi tên hoặc xóa "
                            "ghi chú đó nếu muốn ghi lại)", 4)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.replace(tmp, path)
    return path


def short_ref(ref):
    return re.sub(r"^(refs/(heads|tags|remotes)/)?(origin/)?", "", ref or "")


def check_stamp(stamp):
    stamp = stamp or datetime.datetime.now().strftime(STAMP_FORMAT)
    if not STAMP_RE.match(stamp):
        raise NoteError(f"stamp phải có dạng yyyymmdd-HHMM, nhận: {stamp}")
    return stamp


def note_name(stamp, from_ref, to_ref):
    """<stamp>-<from>~<to>, a name every platform accepts ('/' in a branch -> '-')."""
    return f"{stamp}-{safe_name(short_ref(from_ref))}~{safe_name(short_ref(to_ref))}"


# ---------------------------------------------------------------- section 1: business flows

BUSINESS_HEADING_RE = re.compile(r"^##\s*1\.\s*Nghiệp vụ", re.M)
AFTER_LABEL_RE = re.compile(r"(?<![\w*])(?:\*\*)?(?:Nay|Sau)\b(?:\*\*)?\s*:"      # "Nay:" / "**Sau:**"
                            r"|(?<=→ )(?:nay|sau)\b"                            # "trước ... → nay ..."
                            r"|(?<=[,.;] )(?:nay|sau đó|giờ)\b(?!\s*:)", re.I)   # "trước ..., nay ..."


def split_change(cell):
    """Put the "after" part of a before/after cell on its own line ("... Nay: ...", "**Sau:** ...",
    "trước ... → nay ..."). A Markdown table cell cannot hold a real line break, so it is <br>, which
    Obsidian (and GitHub) render as one."""
    if "<br" in cell.lower():
        return cell
    out, last = [], 0
    for m in AFTER_LABEL_RE.finditer(cell):
        head = re.sub(r"\s*[→,;]?\s*$", "", cell[last:m.start()])
        if not head.strip():
            continue
        out += [head, "<br>"]
        last = m.start()
    out.append(cell[last:])
    return "".join(out)


def _cells(line):
    return [c.strip() for c in re.split(r"(?<!\\)\|", line.strip())[1:-1]]


def _business_table(body):
    """(start, end, lines) of the first table under "## 1. Nghiệp vụ ...", or None."""
    m = BUSINESS_HEADING_RE.search(body)
    if not m:
        return None
    nxt = re.search(r"^## ", body[m.end():], re.M)
    stop = m.end() + nxt.start() if nxt else len(body)
    lines, start, pos = [], None, m.end()
    for line in body[m.end():stop].splitlines(keepends=True):
        if line.lstrip().startswith("|"):
            start = pos if start is None else start
            lines.append(line)
        elif start is not None:
            break
        pos += len(line)
    if start is None or len(lines) < 2:
        return None
    return start, start + sum(len(x) for x in lines), lines


def _column(header, *words):
    return next((i for i, h in enumerate(header) if any(w.lower() in h.lower() for w in words)), None)


def normalize_business(body):
    """Line breaks in the before/after column of section 1's table."""
    found = _business_table(body)
    if not found:
        return body
    start, end, lines = found
    col = _column(_cells(lines[0]), "Thay đổi")
    if col is None:
        return body
    out = lines[:2]
    for line in lines[2:]:
        cells = _cells(line)
        if col < len(cells):
            cells[col] = split_change(cells[col])
        out.append("| " + " | ".join(cells) + " |" + ("\n" if line.endswith("\n") else ""))
    return body[:start] + "".join(out) + body[end:]


def business_rows(body):
    """[(flow, change, level)] of section 1, or [] when it says there is nothing to accept."""
    found = _business_table(body)
    if not found:
        return []
    header = _cells(found[2][0])
    name, change, level = _column(header, "Nghiệp vụ", "luồng"), _column(header, "Thay đổi"), _column(header, "Mức")
    rows = []
    for line in found[2][2:]:
        cells = _cells(line)
        get = lambda i: cells[i] if i is not None and i < len(cells) else ""  # noqa: E731
        if get(name):
            rows.append((get(name), split_change(get(change)), get(level)))
    return rows


def publish(report_md, report_json, stamp=None):
    vault, base, _ = vault_paths()
    stamp = check_stamp(stamp)
    try:
        with open(report_json, encoding="utf-8") as fh:
            data = json.load(fh)
        with open(report_md, encoding="utf-8") as fh:
            body = strip_frontmatter(fh.read())
    except (OSError, ValueError) as exc:
        raise NoteError(f"không đọc được báo cáo: {exc}") from None
    g = data["range"]
    n = g["counts"]
    props = {
        "type": "code-changes", "repo": g["repo"], "stamp": stamp,
        "from": short_ref(g["from_ref"]), "from_kind": g["from_kind"], "from_sha": g["from_sha"][:8],
        "to": short_ref(g["to_ref"]), "to_kind": g["to_kind"], "to_sha": g["to_sha"][:8],
        "date": f"{stamp[:4]}-{stamp[4:6]}-{stamp[6:8]}", "commits": n["commits"], "files": g["files"],
        "endpoints": n["endpoints"], "breaking": n["breaking"], "config_keys": n["config_keys"],
        "migrations": n["migrations"], "entities": n["entities"], "dependencies": n["dependencies"],
        "findings_high": n["findings_high"], "authors": g["authors"],
        "tags": ["code-changes", "service/" + re.sub(r"[^\w/-]+", "-", g["repo"])],
    }
    path = os.path.join(base, safe_name(g["repo"]), note_name(stamp, g["from_ref"], g["to_ref"]) + ".md")
    body = normalize_business(body.lstrip("\n"))
    props["body_hash"] = body_hash(body)
    return write_note(vault, path, frontmatter(props) + body)


def index(stamp, summary=None, include=()):
    """Summary note of a run over several services: every service note carrying this stamp, plus the notes
    named in include (paths relative to the reports folder, e.g. repo/20261115-0231-release~staging.md)."""
    vault, base, _ = vault_paths()
    stamp = check_stamp(stamp)
    rows = []
    if os.path.isdir(base):
        for repo in sorted(os.listdir(base)):
            folder = os.path.join(base, repo)
            if repo == INDEX_DIR or not os.path.isdir(folder):
                continue
            for name in sorted(os.listdir(folder)):
                if not (name.startswith(stamp + "-") and name.endswith(".md")):
                    continue
                props = read_props(os.path.join(folder, name))
                if props and props.get("type") == "code-changes":
                    rows.append((os.path.join(folder, name), props))
    for rel in include:
        path = os.path.realpath(os.path.join(base, rel))
        props = read_props(path) if inside(vault, path) else None
        if not props or props.get("type") != "code-changes":
            raise NoteError(f"không phải ghi chú service do plugin này tạo: {rel}")
        if all(path != r[0] for r in rows):
            rows.append((path, props))
    if not rows:
        raise NoteError(f"không có ghi chú service nào của plugin này với stamp {stamp} trong {base}")
    rows.sort(key=lambda r: r[1].get("repo") or "")
    labels = {(p.get("from"), p.get("to")) for _, p in rows}
    label = "{}~{}".format(*next(iter(labels))) if len(labels) == 1 else "nhieu-moc"
    title = f"{stamp} {label}"
    lines = [frontmatter({"type": "code-changes-index", "stamp": stamp, "date": f"{stamp[:4]}-{stamp[4:6]}-{stamp[6:8]}",
                          "services": len(rows), "tags": ["code-changes"]}),
             f"# Tổng hợp thay đổi {title}", ""]
    if summary:
        try:
            with open(summary, encoding="utf-8") as fh:
                lines += [strip_frontmatter(fh.read()).strip(), ""]
        except OSError as exc:
            raise NoteError(f"không đọc được {summary}: {exc.strerror or exc}") from None
    lines += ["## Nghiệp vụ cần nghiệm thu", ""]
    for path, p in rows:
        link = os.path.relpath(path, vault)[:-3].replace(os.sep, "/")
        with open(path, encoding="utf-8") as fh:
            flows = business_rows(strip_frontmatter(fh.read()))
        lines += [f"### [[{link}\\|{p.get('repo')}]]", ""]
        if flows:
            lines += ["| Nghiệp vụ / luồng | Thay đổi (trước → sau) | Mức |", "|---|---|---|"]
            lines += [f"| {a} | {b} | {c} |" for a, b, c in flows]
        else:
            lines.append("Không có nghiệp vụ cần nghiệm thu.")
        lines.append("")
    lines += ["## Các service", "",
              "| Service | Từ → Đến | Commit | Endpoint | Breaking | Config | Migration | Cảnh báo cao |",
              "|---|---|---:|---:|---:|---:|---:|---:|"]
    for path, p in rows:
        link = os.path.relpath(path, vault)[:-3].replace(os.sep, "/")
        lines.append(f"| [[{link}\\|{p.get('repo')}]] | `{p.get('from')}` → `{p.get('to')}` | {p.get('commits')} | "
                     f"{p.get('endpoints')} | {p.get('breaking')} | {p.get('config_keys')} | {p.get('migrations')} | "
                     f"{p.get('findings_high')} |")
    rel_base = os.path.relpath(base, vault).replace(os.sep, "/")
    lines += ["", "## Truy vấn", "", "```dataview",
              "TABLE repo, from, to, commits, breaking, migrations, findings_high AS \"cảnh báo cao\"",
              f'FROM "{rel_base}"', f'WHERE type = "code-changes" AND stamp = "{stamp}"', "SORT repo ASC", "```", ""]
    name = f"{stamp}-{safe_name(label)}.md"
    text = "\n".join(lines)
    text = text.replace("generated_by: branch-compare\n---", f'body_hash: "{body_hash(text)}"\ngenerated_by: branch-compare\n---', 1)
    return write_note(vault, os.path.join(base, INDEX_DIR, name), text)


def _git(vault, *args):
    proc = subprocess.run(["git", "-C", vault, *args], capture_output=True, text=True)
    return proc.returncode, proc.stdout.strip()


def push_state(vault):
    """', đã đẩy lên remote' / ', còn N commit chưa đẩy' against the upstream, else origin/<branch>; '' unknown."""
    code, ahead = _git(vault, "rev-list", "--count", "@{u}..HEAD")
    if code:
        branch = _git(vault, "rev-parse", "--abbrev-ref", "HEAD")[1]
        code, ahead = _git(vault, "rev-list", "--count", f"origin/{branch}..HEAD")
    if code:
        return ""
    return ", đã đẩy lên remote" if ahead == "0" else f", còn {ahead} commit chưa đẩy"


def sync(timeout=90, poll=2.0):
    """Run the vault's sync command in Obsidian; -> {status, detail}. status: synced, started, off."""
    vault, base, _ = vault_paths()
    mode, _ = setting("BRANCH_COMPARE_OBSIDIAN_SYNC", "on")
    if mode.strip().lower() in ("off", "false", "0", "no"):
        return {"status": "off", "detail": "BRANCH_COMPARE_OBSIDIAN_SYNC=off"}
    cli, _ = setting("OBSIDIAN_CLI", "obsidian")
    command, _ = setting("BRANCH_COMPARE_OBSIDIAN_SYNC_COMMAND", "git-vault-sync:sync")
    exe = shutil.which(os.path.expanduser(cli))
    if not exe:
        raise NoteError(f"không thấy Obsidian CLI `{cli}` (Obsidian > Settings > General > Command line "
                        "interface, hoặc đặt OBSIDIAN_CLI): ghi chú chưa được đồng bộ", 5)
    rel = os.path.relpath(base, vault)
    is_git = _git(vault, "rev-parse", "--is-inside-work-tree")[1] == "true"
    pending = bool(_git(vault, "status", "--porcelain", "--", rel)[1]) if is_git else True
    try:
        proc = subprocess.run([exe, "command", f"id={command}", f"vault={os.path.basename(vault)}"],
                              capture_output=True, text=True, timeout=60)
    except subprocess.TimeoutExpired:
        raise NoteError("Obsidian CLI không trả lời sau 60 giây", 5) from None
    out = "\n".join(x for x in (proc.stdout.strip(), proc.stderr.strip()) if x)
    if "unable to find Obsidian" in out or "not running" in out.lower():
        raise NoteError("Obsidian chưa chạy: ghi chú đã nằm trong vault, sẽ được đồng bộ khi mở Obsidian "
                        "(nếu plugin bật sync on startup) hoặc khi chạy lại lệnh sync", 5)
    if proc.returncode != 0 or "Executed" not in out:
        last = out.splitlines()[-1] if out else f"exit {proc.returncode}"
        raise NoteError(f"lệnh đồng bộ `{command}` lỗi: {last}")
    if not is_git:
        return {"status": "started", "detail": f"đã chạy {command}; vault không phải git repo nên không kiểm được"}
    if not pending:
        head = _git(vault, "rev-parse", "HEAD")[1]
        return {"status": "synced",
                "detail": f"không có ghi chú mới cần commit, HEAD {head[:8]}{push_state(vault)}"}
    deadline = time.monotonic() + timeout
    while _git(vault, "status", "--porcelain", "--", rel)[1]:
        if time.monotonic() >= deadline:
            raise NoteError(f"đã chạy {command} nhưng sau {timeout} giây ghi chú vẫn chưa được commit "
                            "(xem thông báo của plugin trong Obsidian)", 6)
        time.sleep(poll)
    # The plugin commits first, then merges the remote and pushes: wait for the push too.
    state = push_state(vault)
    while state.startswith(", còn") and time.monotonic() < deadline:
        time.sleep(poll)
        state = push_state(vault)
    head = _git(vault, "rev-parse", "HEAD")[1]
    return {"status": "synced", "detail": f"commit {head[:8]}{state}"}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Write change reports into an Obsidian vault.")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("check", help="show where notes go; exit 3 when the vault is not configured")
    sub.add_parser("stamp", help="print a run stamp (yyyymmdd-HHMM, now)")
    p = sub.add_parser("publish", help="write one service report into <dir>/<repo>/")
    p.add_argument("--report", required=True, help="the Markdown report")
    p.add_argument("--json", required=True, help="the ref_changes.py JSON of the same service")
    p.add_argument("--stamp", help="yyyymmdd-HHMM shared by the run (default: now)")
    p = sub.add_parser("index", help="summary note of a run over several services")
    p.add_argument("--stamp", required=True)
    p.add_argument("--summary", help="Markdown inserted at the top of the summary note")
    p.add_argument("--include", action="append", default=[],
                   help="another service note to list, relative to the reports folder (repeatable)")
    p = sub.add_parser("sync", help="run the vault's sync command in Obsidian and wait for the commit")
    p.add_argument("--timeout", type=int, default=90, help="seconds to wait for the commit (default 90)")
    args = parser.parse_args(argv)
    try:
        if args.command == "check":
            vault, base, source = vault_paths()
            print(f"vault: {vault}  ({'environment' if source == 'env' else dev_cli_env_path()})")
            print(f"folder: {base}")
            if not os.path.isdir(os.path.join(vault, ".obsidian")):
                print("warning: không thấy .obsidian/ trong vault (đúng thư mục vault chưa?)")
        elif args.command == "stamp":
            print(check_stamp(None))
        elif args.command == "publish":
            print(publish(args.report, args.json, args.stamp))
        elif args.command == "sync":
            result = sync(args.timeout)
            print(f"{result['status']}: {result['detail']}")
        else:
            print(index(args.stamp, args.summary, args.include))
    except NoteError as exc:
        print(f"obsidian_note: {exc}", file=sys.stderr)
        return exc.code
    except OSError as exc:
        print(f"obsidian_note: {exc.strerror or exc}: {getattr(exc, 'filename', '')}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
