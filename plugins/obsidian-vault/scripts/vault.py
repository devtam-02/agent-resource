#!/usr/bin/env python3
"""Read, write and sync the notes of an Obsidian vault.

Reading and writing work on the files directly, so Obsidian does not have to be running. Sync asks the
running Obsidian app, through its command line, to run the vault's sync plugin (default Git Vault Sync),
then waits until the changes are committed and pushed.

Settings (~/.dev-cli/.env, or the process environment):
  OBSIDIAN_VAULT          vault folder, the one holding .obsidian/. Required.
  OBSIDIAN_VAULT_NAME     vault name as Obsidian knows it. Default: the folder name
  OBSIDIAN_INBOX          folder for a new note given without a folder. Default: the vault root
  OBSIDIAN_CLI            Obsidian's command line (Settings > General > CLI). Default: obsidian
  OBSIDIAN_SYNC_COMMAND   Obsidian command id that syncs the vault. Default: git-vault-sync:sync
  OBSIDIAN_SYNC_TIMEOUT   seconds sync waits for the commit and the push. Default: 90
  OBSIDIAN_BACKUP_DIR     where --overwrite keeps the previous version. Default: ~/.cache/obsidian-vault/backups
  OBSIDIAN_AUTO_SYNC      on: write/append/prop print `auto-sync: on`, telling the agent to run sync. Default: off

  vault.py doctor | status
  vault.py list [FOLDER] [--recent N] [--tag T] [--prop K=V]
  vault.py search TEXT [--folder F] [--tag T] [--prop K=V] [--regex] [--case] [--limit N]
  vault.py read NOTE [--section HEADING] [--props] [--outline] [--max-lines N]
  vault.py links NOTE
  vault.py daily [--date YYYY-MM-DD]
  vault.py write NOTE (--stdin | --file F) [--prop K=V ...] [--overwrite]
  vault.py append NOTE (--stdin | --text T | --file F) [--section HEADING] [--create]
  vault.py prop NOTE [--set K=V] [--list K=A,B] [--add K=V] [--unset K]
  vault.py sync [--timeout N] [--no-wait]

NOTE is a path relative to the vault (folder/note.md, the .md may be left out) or a bare note name,
resolved like a wikilink. .obsidian/, .git/ and .trash/ are never read or written.

Exit codes: 0 ok, 2 usage or bad name, 3 vault not configured or not found, 4 note not found, ambiguous,
or already exists, 5 Obsidian not running or its CLI missing (nothing synced), 6 sync started but not
committed/pushed in time, 7 the vault has merge conflicts.
"""

import argparse
import datetime
import fnmatch
import json
import os
import re
import shutil
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from plugin_settings import dev_cli_env_path, setting  # noqa: E402

TEXT_EXT = (".md", ".txt", ".sql", ".csv", ".canvas", ".json", ".yaml", ".yml", ".xml", ".log")
SKIP_DIRS = {".obsidian", ".git", ".trash", "node_modules"}
BAD_CHARS = re.compile(r'[\\:*?"<>|#^\[\]\x00-\x1f]')
HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
FENCE_RE = re.compile(r"^\s*(```|~~~)")
WIKILINK_RE = re.compile(r"(!?)\[\[([^\]|#^]*)(?:[#^][^\]|]*)?(?:\|[^\]]*)?\]\]")
MDLINK_RE = re.compile(r"\[[^\]]*\]\(([^)\s]+\.md)(?:#[^)]*)?\)")
INLINE_TAG_RE = re.compile(r"(?:^|\s)#([\w/-]*[^\W\d_][\w/-]*)", re.UNICODE)
UNMERGED = {"DD", "AU", "UD", "UA", "DU", "AA", "UU"}
SECRET_FILES = (".obsidian/plugins/git-vault-sync/data.json",)


class VaultError(Exception):
    def __init__(self, message, code=2):
        super().__init__(message)
        self.code = code


# ---------------------------------------------------------------- vault and paths

def vault_root():
    raw, source = setting("OBSIDIAN_VAULT")
    if not raw:
        raise VaultError(f"chưa cấu hình OBSIDIAN_VAULT (thêm OBSIDIAN_VAULT=<thư mục vault> vào {dev_cli_env_path()})", 3)
    root = os.path.realpath(os.path.expanduser(raw))
    if not os.path.isdir(root):
        raise VaultError(f"OBSIDIAN_VAULT không phải thư mục: {root}", 3)
    return root


def rel(root, path):
    return os.path.relpath(path, root).replace(os.sep, "/")


def blocked(relpath):
    parts = relpath.replace("\\", "/").split("/")
    return any(p in SKIP_DIRS for p in parts) or ".." in parts


def inside(root, path):
    real = os.path.realpath(path)
    return real == root or real.startswith(root + os.sep)


def check_new_path(root, relpath):
    """Path of a note to create or change: inside the vault, not in a hidden system folder, portable name."""
    relpath = relpath.strip().replace("\\", "/").lstrip("/")
    if not relpath:
        raise VaultError("thiếu tên ghi chú")
    if os.path.splitext(relpath)[1].lower() not in TEXT_EXT:
        relpath += ".md"
    if blocked(relpath):
        raise VaultError(f"không ghi vào .obsidian/, .git/, .trash/ hoặc ra ngoài vault: {relpath}")
    for part in relpath.split("/"):
        if not part or part.startswith(".") or BAD_CHARS.search(part) or part != part.strip() or part.endswith("."):
            raise VaultError(f"tên không hợp lệ `{part}` trong {relpath}: không dùng \\ : * ? \" < > | # ^ [ ], "
                             "không bắt đầu bằng dấu chấm, không có khoảng trắng ở đầu/cuối (thay bằng `-`)")
    path = os.path.join(root, *relpath.split("/"))
    existing = os.path.dirname(path)
    while not os.path.exists(existing):
        existing = os.path.dirname(existing)
    if not inside(root, existing) or (os.path.lexists(path) and not inside(root, path)):
        raise VaultError(f"đường dẫn ra ngoài vault (symlink?): {relpath}")
    return path


def walk(root, folder=None, ext=TEXT_EXT):
    """Yield absolute paths of the files under the vault (or one folder of it), sorted, hidden folders skipped."""
    start = root
    if folder:
        folder = folder.strip("/")
        if blocked(folder):
            raise VaultError(f"không đọc thư mục hệ thống: {folder}")
        start = os.path.join(root, *folder.split("/"))
        if not os.path.isdir(start) or not inside(root, start):
            raise VaultError(f"không có thư mục {folder} trong vault", 4)
    for dirpath, dirnames, filenames in os.walk(start):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS and not d.startswith("."))
        for name in sorted(filenames):
            if name.lower().endswith(ext) and not name.startswith("."):
                yield os.path.join(dirpath, name)


def resolve(root, name):
    """An existing note from a vault-relative path or a bare name (like a wikilink)."""
    name = name.strip().replace("\\", "/").lstrip("/")
    if name.startswith("[[") and name.endswith("]]"):
        name = name[2:-2].split("|")[0].split("#")[0]
    if not name:
        raise VaultError("thiếu tên ghi chú")
    if blocked(name):
        raise VaultError(f"không đọc .obsidian/, .git/, .trash/: {name}")
    candidates = [name] if os.path.splitext(name)[1].lower() in TEXT_EXT else [name + ".md", name]
    for c in candidates:
        path = os.path.join(root, *c.split("/"))
        if os.path.isfile(path) and inside(root, path):
            return path
    if "/" in name:
        raise VaultError(f"không có ghi chú {name}", 4)
    stem = name[:-3] if name.lower().endswith(".md") else name
    hits = [p for p in walk(root) if os.path.splitext(os.path.basename(p))[0].lower() == stem.lower()]
    if len(hits) == 1:
        return hits[0]
    if not hits:
        raise VaultError(f"không có ghi chú tên {name} (thử: vault.py search \"{stem}\")", 4)
    raise VaultError("nhiều ghi chú cùng tên, ghi rõ đường dẫn:\n  " + "\n  ".join(rel(root, h) for h in hits), 4)


def read_text(path):
    with open(path, encoding="utf-8", errors="replace") as fh:
        return fh.read()


def clean(text):
    """Drop terminal escape sequences and control characters (note content is data, printed as is)."""
    text = re.sub(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(\x07|\x1b\\)", "", text)
    return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)


def atomic_write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = os.path.join(os.path.dirname(path), f".{os.path.basename(path)}.{os.getpid()}.tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text if text.endswith("\n") else text + "\n")
    os.replace(tmp, path)


def backup(root, path):
    base, _ = setting("OBSIDIAN_BACKUP_DIR", "~/.cache/obsidian-vault/backups")
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = os.path.join(os.path.expanduser(base), stamp, rel(root, path))
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    shutil.copy2(path, dest)
    return dest


# ---------------------------------------------------------------- properties (YAML frontmatter)

def split_frontmatter(text):
    """-> (frontmatter lines without the --- fences, or None; body)."""
    if text.startswith("---\n") or text.startswith("---\r\n"):
        lines = text.splitlines(keepends=True)
        for i in range(1, len(lines)):
            if lines[i].rstrip("\r\n") in ("---", "..."):
                return [l.rstrip("\r\n") for l in lines[1:i]], "".join(lines[i + 1:])
    return None, text


def _scalar(raw):
    raw = raw.strip()
    if not raw:
        return None
    if raw[0] in "\"'" and raw[-1] == raw[0] and len(raw) >= 2:
        if raw[0] == '"':
            try:
                return json.loads(raw)
            except ValueError:
                return raw[1:-1]
        return raw[1:-1].replace("''", "'")
    if raw.startswith("[") and raw.endswith("]"):
        return [_scalar(x) for x in raw[1:-1].split(",") if x.strip()]
    return raw


def _entries(fm):
    """[(key, first line, end line exclusive)] of the top-level keys."""
    out, i = [], 0
    while i < len(fm):
        m = re.match(r"^([^\s#:][^:]*):(\s|$)", fm[i])
        if not m:
            i += 1
            continue
        j = i + 1
        while j < len(fm) and (fm[j].startswith((" ", "\t", "-")) or not fm[j].strip()):
            j += 1
        out.append((m.group(1).strip(), i, j))
        i = j
    return out


def parse_props(fm):
    props = {}
    for key, i, j in _entries(fm or []):
        value = fm[i].split(":", 1)[1]
        if value.strip():
            props[key] = _scalar(value)
        else:
            items = [l.strip()[1:].strip() for l in fm[i + 1:j] if l.strip().startswith("-")]
            props[key] = [_scalar(x) for x in items] if items else None
    return props


def yaml_scalar(value):
    if re.fullmatch(r"-?\d+(\.\d+)?|true|false|null", value):
        return value
    if (not value or value != value.strip() or value[0] in "!&*-?{}[]|>%@`,'\"#"
            or ": " in value or " #" in value or value.endswith(":")):
        return json.dumps(value, ensure_ascii=False)
    return value


def note_tags(props, body):
    tags = set()
    raw = props.get("tags") if props else None
    if isinstance(raw, str):
        raw = re.split(r"[,\s]+", raw)
    for t in raw or []:
        if t:
            tags.add(str(t).lstrip("#").lower())
    in_fence = False
    for line in body.splitlines():
        if FENCE_RE.match(line):
            in_fence = not in_fence
        elif not in_fence:
            tags.update(t.lower() for t in INLINE_TAG_RE.findall(line))
    return tags


def matches_filters(text, tag=None, prop=None):
    if not tag and not prop:
        return True
    fm, body = split_frontmatter(text)
    props = parse_props(fm)
    if tag:
        want = tag.lstrip("#").lower()
        if not any(t == want or t.startswith(want + "/") for t in note_tags(props, body)):
            return False
    if prop:
        key, _, want = prop.partition("=")
        have = props.get(key.strip())
        values = have if isinstance(have, list) else [have]
        if want:
            return any(str(v).lower() == want.strip().lower() for v in values if v is not None)
        return have is not None
    return True


def edit_props(text, sets=(), lists=(), adds=(), unsets=()):
    fm, body = split_frontmatter(text)
    fm = list(fm or [])

    def find(key):
        for k, i, j in _entries(fm):
            if k == key:
                return i, j
        return None

    def put(key, lines):
        span = find(key)
        if span:
            fm[span[0]:span[1]] = lines
        else:
            fm.extend(lines)

    for item in sets:
        key, sep, value = item.partition("=")
        if not sep or not key.strip():
            raise VaultError(f"--set cần dạng KEY=VALUE: {item}")
        put(key.strip(), [f"{key.strip()}: {yaml_scalar(value.strip())}"])
    for item in lists:
        key, sep, value = item.partition("=")
        if not sep or not key.strip():
            raise VaultError(f"--list cần dạng KEY=A,B: {item}")
        values = [v.strip() for v in value.split(",") if v.strip()]
        put(key.strip(), [f"{key.strip()}:"] + [f"  - {yaml_scalar(v)}" for v in values])
    for item in adds:
        key, sep, value = item.partition("=")
        if not sep or not key.strip() or not value.strip():
            raise VaultError(f"--add cần dạng KEY=VALUE: {item}")
        key, value = key.strip(), value.strip()
        current = parse_props(fm).get(key)
        values = current if isinstance(current, list) else ([] if current is None else [current])
        values = [str(v) for v in values if v is not None]
        if value not in values:
            values.append(value)
        put(key, [f"{key}:"] + [f"  - {yaml_scalar(v)}" for v in values])
    for key in unsets:
        span = find(key.strip())
        if span:
            del fm[span[0]:span[1]]
    if not fm:
        return body
    return "---\n" + "\n".join(fm) + "\n---\n" + body


# ---------------------------------------------------------------- sections

def heading_lines(lines):
    """[(index, level, title)] outside fenced code blocks."""
    out, in_fence = [], False
    for i, line in enumerate(lines):
        if FENCE_RE.match(line):
            in_fence = not in_fence
            continue
        m = None if in_fence else HEADING_RE.match(line)
        if m:
            out.append((i, len(m.group(1)), m.group(2).strip()))
    return out


def find_section(lines, heading):
    """-> (heading index, end index exclusive) of the first heading whose title matches."""
    want = heading.lstrip("#").strip().lower()
    heads = heading_lines(lines)
    for n, (i, level, title) in enumerate(heads):
        if title.lower() == want:
            end = len(lines)
            for j, lv, _ in heads[n + 1:]:
                if lv <= level:
                    end = j
                    break
            return i, end
    titles = ", ".join(t for _, _, t in heads[:15]) or "không có tiêu đề nào"
    raise VaultError(f"không có mục `{heading}` (các mục: {titles})", 4)


# ---------------------------------------------------------------- git

def git(root, *args, timeout=30):
    try:
        proc = subprocess.run(["git", "-C", root, *args], capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return 1, ""
    return proc.returncode, proc.stdout.rstrip("\n")


def is_git(root):
    return git(root, "rev-parse", "--is-inside-work-tree")[1] == "true"


def excluded_paths(root):
    """excludePaths of Git Vault Sync (only that key is read from its settings file)."""
    try:
        with open(os.path.join(root, ".obsidian", "plugins", "git-vault-sync", "data.json"), encoding="utf-8") as fh:
            raw = json.load(fh).get("excludePaths", "")
    except (OSError, ValueError, AttributeError):
        return []
    items = raw if isinstance(raw, list) else str(raw).splitlines()
    return [x.strip() for x in items if x and x.strip()]


def is_excluded(path, patterns):
    for p in patterns:
        if p.endswith("/") and (path + "/").startswith(p):
            return True
        if path == p or fnmatch.fnmatch(path, p) or fnmatch.fnmatch(os.path.basename(path), p):
            return True
    return False


def porcelain(root):
    """[(XY, path)] of git status, untracked included."""
    code, out = git(root, "status", "--porcelain=v1", "-uall", "-z")
    if code:
        return []
    items, parts, i = [], out.split("\0"), 0
    while i < len(parts):
        entry = parts[i]
        if len(entry) > 3:
            xy, path = entry[:2], entry[3:]
            items.append((xy, path))
            if xy[0] in "RC":
                i += 1
        i += 1
    return items


def pending(root):
    patterns = excluded_paths(root)
    return [(xy, p) for xy, p in porcelain(root) if not is_excluded(p, patterns)]


def conflicts(root):
    return [p for xy, p in porcelain(root) if xy in UNMERGED]


def ahead_behind(root):
    """-> (ahead, behind, upstream) or (None, None, None)."""
    upstream = "@{u}"
    code, _ = git(root, "rev-parse", "--verify", "--quiet", upstream)
    if code:
        branch = git(root, "rev-parse", "--abbrev-ref", "HEAD")[1]
        upstream = f"origin/{branch}"
        if git(root, "rev-parse", "--verify", "--quiet", upstream)[0]:
            return None, None, None
    code, out = git(root, "rev-list", "--left-right", "--count", f"HEAD...{upstream}")
    if code or not out:
        return None, None, None
    ahead, behind = (int(x) for x in out.split())
    return ahead, behind, upstream


def mask_url(url):
    return re.sub(r"//[^/@\s]+@", "//***@", url)


# ---------------------------------------------------------------- commands

def cmd_doctor(args):
    root = vault_root()
    src = setting("OBSIDIAN_VAULT")[1]
    print(f"vault: {root}  ({'environment' if src == 'env' else dev_cli_env_path()})")
    print(f"name: {setting('OBSIDIAN_VAULT_NAME', os.path.basename(root))[0]}")
    print(f".obsidian/: {'có' if os.path.isdir(os.path.join(root, '.obsidian')) else 'KHÔNG (đúng thư mục vault chưa?)'}")
    notes = sum(1 for p in walk(root) if p.endswith(".md"))
    print(f"notes: {notes}")
    inbox = setting("OBSIDIAN_INBOX")[0]
    print(f"inbox: {inbox or '(gốc vault)'}")
    print(f"auto-sync: {setting('OBSIDIAN_AUTO_SYNC', 'off')[0]}")
    plugins = []
    try:
        with open(os.path.join(root, ".obsidian", "community-plugins.json"), encoding="utf-8") as fh:
            plugins = json.load(fh)
    except (OSError, ValueError):
        pass
    command = setting("OBSIDIAN_SYNC_COMMAND", "git-vault-sync:sync")[0]
    plugin_id = command.split(":", 1)[0]
    print(f"sync command: {command}  (plugin {plugin_id} {'đang bật' if plugin_id in plugins else 'CHƯA bật trong vault'})")
    cli = setting("OBSIDIAN_CLI", "obsidian")[0]
    exe = shutil.which(os.path.expanduser(cli))
    print(f"obsidian cli: {exe or f'không thấy `{cli}` (Settings > General > Command line interface, hoặc OBSIDIAN_CLI)'}")
    if is_git(root):
        remote = git(root, "remote", "get-url", "origin")[1]
        print(f"git: {git(root, 'rev-parse', '--abbrev-ref', 'HEAD')[1]}  remote {mask_url(remote) or '(chưa có)'}")
        for f in SECRET_FILES:
            if os.path.exists(os.path.join(root, f)) and git(root, "check-ignore", "-q", f)[0] != 0 \
                    and git(root, "ls-files", "--error-unmatch", f)[0] != 0:
                print(f"warning: {f} (có token) chưa nằm trong .gitignore của vault; plugin tự loại trừ, "
                      "nhưng một lệnh `git add -A` sẽ commit nó")
    else:
        print("git: vault không phải git repo (sync không kiểm được commit)")
    return 0


def cmd_status(args):
    root = vault_root()
    if not is_git(root):
        raise VaultError("vault không phải git repo", 3)
    branch = git(root, "rev-parse", "--abbrev-ref", "HEAD")[1]
    head = git(root, "log", "-1", "--format=%h %ad %s", "--date=format:%Y-%m-%d %H:%M")[1]
    ahead, behind, upstream = ahead_behind(root)
    print(f"branch {branch} · HEAD {head}")
    if upstream:
        print(f"so với {upstream}: chưa đẩy {ahead} commit, chưa kéo {behind} commit (theo lần fetch gần nhất)")
    bad = conflicts(root)
    if bad:
        print(f"CONFLICT ({len(bad)}): " + ", ".join(bad))
    items = pending(root)
    print(f"thay đổi chưa commit (trừ excludePaths của plugin): {len(items)}")
    for xy, p in items[:30]:
        print(f"  {xy} {p}")
    if len(items) > 30:
        print(f"  ... và {len(items) - 30} file khác")
    return 7 if bad else 0


def cmd_list(args):
    root = vault_root()
    rows = []
    for path in walk(root, args.folder):
        text = None
        if args.tag or args.prop:
            if not path.endswith(".md"):
                continue
            text = read_text(path)
            if not matches_filters(text, args.tag, args.prop):
                continue
        st = os.stat(path)
        rows.append((st.st_mtime, st.st_size, rel(root, path)))
    if args.recent:
        rows = sorted(rows, reverse=True)[:args.recent]
    for mtime, size, path in rows[:args.limit]:
        print(f"{datetime.datetime.fromtimestamp(mtime):%Y-%m-%d %H:%M}  {size:>8}  {path}")
    if len(rows) > args.limit:
        print(f"... {len(rows) - args.limit} file khác (thu hẹp bằng thư mục, --tag, --prop hoặc --limit)")
    if not rows:
        print("không có ghi chú nào khớp")
    return 0


def cmd_search(args):
    root = vault_root()
    flags = 0 if args.case else re.IGNORECASE
    try:
        pattern = re.compile(args.text if args.regex else re.escape(args.text), flags)
    except re.error as exc:
        raise VaultError(f"regex sai: {exc}") from None
    shown, files = 0, 0
    for path in walk(root, args.folder):
        relpath = rel(root, path)
        if relpath.lower().endswith(".excalidraw.md") and not pattern.search(relpath):
            continue
        try:
            text = read_text(path)
        except OSError:
            continue
        if args.tag or args.prop:
            if not path.endswith(".md") or not matches_filters(text, args.tag, args.prop):
                continue
        name_hit = bool(pattern.search(relpath))
        hits = [(n, line) for n, line in enumerate(text.splitlines(), 1) if pattern.search(line)]
        if not hits and not name_hit:
            continue
        files += 1
        if shown >= args.limit:
            continue
        print(f"{relpath}" + (f"  ({len(hits)} dòng)" if hits else "  (khớp tên)"))
        for n, line in hits[:args.per_file]:
            line = clean(line.strip())
            if len(line) > 200:
                m = pattern.search(line)
                start = max(0, (m.start() if m else 0) - 80)
                line = ("…" if start else "") + line[start:start + 200] + "…"
            print(f"  {n}: {line}")
        shown += 1
    if not files:
        print("không tìm thấy")
        return 4
    if files > shown:
        print(f"... thêm {files - shown} file khớp (thu hẹp bằng --folder, --tag, hoặc tăng --limit)")
    return 0


def cmd_read(args):
    root = vault_root()
    path = resolve(root, args.note)
    text = read_text(path)
    print(f"# file: {rel(root, path)}")
    fm, body = split_frontmatter(text)
    if args.props:
        print(json.dumps(parse_props(fm), ensure_ascii=False, indent=2))
        return 0
    lines = text.splitlines()
    if args.outline:
        for _, level, title in heading_lines(body.splitlines()):
            print("  " * (level - 1) + f"- {title}")
        return 0
    if args.section:
        start, end = find_section(lines, args.section)
        lines = lines[start:end]
    total = len(lines)
    for line in lines[:args.max_lines]:
        print(clean(line))
    if total > args.max_lines:
        print(f"... còn {total - args.max_lines} dòng (đọc tiếp bằng --section, hoặc tăng --max-lines)")
    return 0


def _link_target(root, target, source):
    target = target.strip()
    if not target:
        return rel(root, source)
    try:
        return rel(root, resolve(root, target))
    except VaultError:
        return None


def cmd_links(args):
    root = vault_root()
    path = resolve(root, args.note)
    me = rel(root, path)
    text = read_text(path)
    out = []
    for m in WIKILINK_RE.finditer(text):
        out.append(("embed" if m.group(1) else "link", m.group(2).strip()))
    for m in MDLINK_RE.finditer(text):
        target = os.path.normpath(os.path.join(os.path.dirname(me), m.group(1))).replace(os.sep, "/")
        out.append(("md", target))
    print(f"# {me}")
    print(f"liên kết đi ({len(out)}):")
    seen = set()
    for kind, target in out:
        if (kind, target) in seen:
            continue
        seen.add((kind, target))
        found = _link_target(root, target, path)
        print(f"  {kind:5} {target}" + (f" -> {found}" if found and found != target else "" if found else "  (chưa có ghi chú)"))
    stem = os.path.splitext(os.path.basename(me))[0].lower()
    no_ext = os.path.splitext(me)[0].lower()
    back = []
    for other in walk(root, ext=(".md", ".canvas")):
        if other == path:
            continue
        body = read_text(other)
        for m in WIKILINK_RE.finditer(body):
            t = m.group(2).strip().lower()
            if t in (stem, no_ext, me.lower()):
                back.append(rel(root, other))
                break
        else:
            if any(me.lower().endswith(x.lower().lstrip("./")) for x in MDLINK_RE.findall(body)):
                back.append(rel(root, other))
    print(f"liên kết đến ({len(back)}):")
    for b in back:
        print(f"  {b}")
    return 0


MOMENT_TOKENS = [("YYYY", "%Y"), ("YY", "%y"), ("MMMM", "%B"), ("MMM", "%b"), ("MM", "%m"), ("DD", "%d"),
                 ("dddd", "%A"), ("ddd", "%a"), ("HH", "%H"), ("mm", "%M")]


def moment_format(fmt, day):
    out, i = [], 0
    while i < len(fmt):
        if fmt[i] == "[":
            j = fmt.find("]", i)
            if j < 0:
                raise VaultError(f"định dạng ngày không đọc được: {fmt}")
            out.append(fmt[i + 1:j])
            i = j + 1
            continue
        for token, code in MOMENT_TOKENS:
            if fmt.startswith(token, i):
                out.append(day.strftime(code))
                i += len(token)
                break
        else:
            if fmt[i] in "MDdHhmsWwQXxGgEeAaZz":
                if fmt[i] in "MD":
                    out.append(str(day.month if fmt[i] == "M" else day.day))
                    i += 1
                    continue
                raise VaultError(f"định dạng ngày `{fmt}` có ký hiệu chưa hỗ trợ `{fmt[i]}`: ghi rõ đường dẫn ghi chú")
            out.append(fmt[i])
            i += 1
    return "".join(out)


def cmd_daily(args):
    root = vault_root()
    conf = {}
    try:
        with open(os.path.join(root, ".obsidian", "daily-notes.json"), encoding="utf-8") as fh:
            conf = json.load(fh)
    except (OSError, ValueError):
        pass
    day = datetime.date.fromisoformat(args.date) if args.date else datetime.date.today()
    name = moment_format(conf.get("format") or "YYYY-MM-DD", day)
    folder = (conf.get("folder") or "").strip("/")
    relpath = f"{folder}/{name}.md" if folder else f"{name}.md"
    exists = os.path.isfile(os.path.join(root, *relpath.split("/")))
    print(f"{relpath}  ({'đã có' if exists else 'chưa có'})")
    template = conf.get("template")
    if template and not exists:
        print(f"template: {template} (Obsidian dùng khi tạo từ app; tạo bằng script thì đọc template rồi ghi)")
    return 0


BLOCK_RE = re.compile(r"^\s*([-*+]\s|\d+[.)]\s|\|)")


def same_block(previous, first):
    """True when the appended text continues the list or table that ends the note or section."""
    a, b = BLOCK_RE.match(previous), BLOCK_RE.match(first)
    return bool(a and b and (a.group(1)[0] == "|") == (b.group(1)[0] == "|"))


def _content(args):
    if getattr(args, "text", None) is not None:
        return args.text.replace("\\n", "\n")
    if args.file:
        return read_text(args.file)
    if args.stdin:
        return sys.stdin.read()
    raise VaultError("thiếu nội dung: --stdin, --file hoặc --text")


def _target(root, note):
    note = note.strip().replace("\\", "/")
    inbox = setting("OBSIDIAN_INBOX")[0].strip("/")
    if "/" not in note.lstrip("/") and inbox:
        note = f"{inbox}/{note}"
    return check_new_path(root, note)


def auto_sync_hint():
    if setting("OBSIDIAN_AUTO_SYNC", "off")[0].strip().lower() in ("on", "true", "1", "yes"):
        print("auto-sync: on (chạy vault.py sync sau lần ghi cuối)")


def cmd_write(args):
    root = vault_root()
    path = _target(root, args.note)
    content = _content(args)
    if args.prop:
        content = edit_props(content, sets=args.prop)
    saved = None
    if os.path.exists(path):
        if not args.overwrite:
            raise VaultError(f"đã có {rel(root, path)}: không ghi đè (dùng append, prop, tên khác, "
                             "hoặc --overwrite khi người dùng yêu cầu thay cả ghi chú)", 4)
        saved = backup(root, path)
    atomic_write(path, content)
    print(f"{'ghi đè' if saved else 'đã tạo'}: {rel(root, path)}" + (f"  (bản cũ: {saved})" if saved else ""))
    auto_sync_hint()
    return 0


def cmd_append(args):
    root = vault_root()
    try:
        path = resolve(root, args.note)
    except VaultError as exc:
        if exc.code != 4 or not args.create or "nhiều ghi chú" in str(exc):
            raise
        path = _target(root, args.note)
    content = _content(args).strip("\n")
    if not content.strip():
        raise VaultError("nội dung rỗng")
    if not os.path.exists(path):
        heading = f"## {args.section}\n\n" if args.section else ""
        atomic_write(path, heading + content)
        print(f"đã tạo: {rel(root, path)}")
        auto_sync_hint()
        return 0
    text = read_text(path)
    lines = text.splitlines()
    if args.section:
        try:
            start, end = find_section(lines, args.section)
        except VaultError:
            if not args.create:
                raise
            lines += ["", f"## {args.section}", ""]
            start, end = len(lines) - 2, len(lines)
        while end > start + 1 and not lines[end - 1].strip():
            end -= 1
        new = content.split("\n")
        gap = [] if same_block(lines[end - 1], new[0]) else [""]
        tail = [""] if end < len(lines) and lines[end].strip() else []
        lines[end:end] = gap + new + tail
    else:
        while lines and not lines[-1].strip():
            lines.pop()
        new = content.split("\n")
        lines += ([] if not lines or same_block(lines[-1], new[0]) else [""]) + new
    atomic_write(path, "\n".join(lines))
    print(f"đã thêm {len(content.splitlines())} dòng vào {rel(root, path)}" + (f" (mục {args.section})" if args.section else ""))
    auto_sync_hint()
    return 0


def cmd_prop(args):
    root = vault_root()
    path = resolve(root, args.note)
    if not path.endswith(".md"):
        raise VaultError("chỉ ghi chú .md mới có properties")
    if not (args.set or args.list or args.add or args.unset):
        fm, _ = split_frontmatter(read_text(path))
        print(json.dumps(parse_props(fm), ensure_ascii=False, indent=2))
        return 0
    text = read_text(path)
    new = edit_props(text, args.set, args.list, args.add, args.unset)
    if new == text:
        print(f"không đổi: {rel(root, path)}")
        return 0
    atomic_write(path, new)
    fm, _ = split_frontmatter(new)
    print(f"đã cập nhật properties: {rel(root, path)}")
    print(json.dumps(parse_props(fm), ensure_ascii=False))
    auto_sync_hint()
    return 0


def run_sync_command(root):
    cli = setting("OBSIDIAN_CLI", "obsidian")[0]
    command = setting("OBSIDIAN_SYNC_COMMAND", "git-vault-sync:sync")[0]
    name = setting("OBSIDIAN_VAULT_NAME", os.path.basename(root))[0]
    exe = shutil.which(os.path.expanduser(cli))
    if not exe:
        raise VaultError(f"không thấy Obsidian CLI `{cli}` (Obsidian > Settings > General > Command line "
                         "interface, hoặc đặt OBSIDIAN_CLI): chưa đồng bộ", 5)
    try:
        proc = subprocess.run([exe, "command", f"id={command}", f"vault={name}"],
                              capture_output=True, text=True, timeout=60)
    except subprocess.TimeoutExpired:
        raise VaultError("Obsidian CLI không trả lời sau 60 giây", 5) from None
    out = "\n".join(x for x in (proc.stdout.strip(), proc.stderr.strip()) if x)
    if "unable to find obsidian" in out.lower() or "not running" in out.lower():
        raise VaultError("Obsidian chưa chạy: thay đổi đã nằm trong vault, sẽ được đồng bộ khi mở Obsidian "
                         "(nếu plugin bật sync on startup) hoặc khi chạy lại sync", 5)
    if proc.returncode != 0 or "Executed" not in out:
        last = out.splitlines()[-1] if out else f"exit {proc.returncode}"
        raise VaultError(f"lệnh đồng bộ `{command}` lỗi: {clean(last)}")
    return command


def sync(root, timeout, wait=True, poll=2.0):
    """-> one line describing the result; raises VaultError with code 5/6/7."""
    git_repo = is_git(root)
    if git_repo:
        bad = conflicts(root)
        if bad:
            raise VaultError("vault đang có conflict, chưa đồng bộ: " + ", ".join(bad) +
                             " (giải quyết trong Obsidian: Git Vault Sync, hoặc nhờ sửa khi người dùng yêu cầu)", 7)
    before = git(root, "rev-parse", "HEAD")[1] if git_repo else ""
    had_pending = bool(pending(root)) if git_repo else True
    command = run_sync_command(root)
    if not git_repo:
        return f"started: đã chạy {command}; vault không phải git repo nên không kiểm được commit"
    if not wait:
        return f"started: đã chạy {command}, không chờ (xem lại bằng vault.py status)"
    deadline = time.monotonic() + timeout
    while had_pending and pending(root):
        if conflicts(root):
            raise VaultError("đồng bộ gặp conflict: " + ", ".join(conflicts(root)), 7)
        if time.monotonic() >= deadline:
            left = pending(root)
            raise VaultError(f"đã chạy {command} nhưng sau {timeout} giây còn {len(left)} thay đổi chưa commit "
                             f"({', '.join(p for _, p in left[:5])}); xem thông báo của plugin trong Obsidian", 6)
        time.sleep(poll)
    ahead, behind, upstream = ahead_behind(root)
    while ahead and time.monotonic() < deadline:
        time.sleep(poll)
        ahead, behind, upstream = ahead_behind(root)
    if conflicts(root):
        raise VaultError("đồng bộ gặp conflict: " + ", ".join(conflicts(root)), 7)
    head = git(root, "log", "-1", "--format=%h %s")[1]
    state = "" if upstream is None else (", đã đẩy lên remote" if not ahead else f", còn {ahead} commit chưa đẩy")
    if ahead:
        raise VaultError(f"đã commit ({head}) nhưng sau {timeout} giây vẫn chưa đẩy {ahead} commit lên {upstream}", 6)
    changed = "commit mới" if git(root, "rev-parse", "HEAD")[1] != before else "không có commit mới"
    return f"synced: {changed}, HEAD {head}{state}"


def cmd_sync(args):
    root = vault_root()
    timeout = args.timeout
    if timeout is None:
        raw = setting("OBSIDIAN_SYNC_TIMEOUT", "90")[0]
        timeout = int(raw) if raw.isdigit() else 90
    print(sync(root, timeout, wait=not args.no_wait))
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(prog="vault.py", description="Read, write and sync an Obsidian vault.")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("doctor", help="settings, vault, sync plugin, CLI").set_defaults(func=cmd_doctor)
    sub.add_parser("status", help="git state of the vault: pending changes, ahead/behind, conflicts").set_defaults(func=cmd_status)

    p = sub.add_parser("list", help="list notes")
    p.add_argument("folder", nargs="?")
    p.add_argument("--recent", type=int, help="the N most recently modified")
    p.add_argument("--tag")
    p.add_argument("--prop", help="KEY=VALUE, or KEY to require the property")
    p.add_argument("--limit", type=int, default=200)
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("search", help="search note names and content")
    p.add_argument("text")
    p.add_argument("--folder")
    p.add_argument("--tag")
    p.add_argument("--prop")
    p.add_argument("--regex", action="store_true")
    p.add_argument("--case", action="store_true", help="case-sensitive")
    p.add_argument("--limit", type=int, default=30, help="files shown (default 30)")
    p.add_argument("--per-file", type=int, default=5, help="lines shown per file (default 5)")
    p.set_defaults(func=cmd_search)

    p = sub.add_parser("read", help="print a note")
    p.add_argument("note")
    p.add_argument("--section", help="only this heading's section")
    p.add_argument("--props", action="store_true", help="only the properties, as JSON")
    p.add_argument("--outline", action="store_true", help="only the headings")
    p.add_argument("--max-lines", type=int, default=400)
    p.set_defaults(func=cmd_read)

    p = sub.add_parser("links", help="outgoing links and backlinks of a note")
    p.add_argument("note")
    p.set_defaults(func=cmd_links)

    p = sub.add_parser("daily", help="path of the daily note (from .obsidian/daily-notes.json)")
    p.add_argument("--date", help="YYYY-MM-DD, default today")
    p.set_defaults(func=cmd_daily)

    p = sub.add_parser("write", help="create a note (never overwrites without --overwrite)")
    p.add_argument("note")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--stdin", action="store_true")
    g.add_argument("--file")
    p.add_argument("--prop", action="append", default=[], help="KEY=VALUE property (repeatable)")
    p.add_argument("--overwrite", action="store_true", help="replace an existing note; the old one is backed up")
    p.set_defaults(func=cmd_write)

    p = sub.add_parser("append", help="append to a note, or to one section of it")
    p.add_argument("note")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--stdin", action="store_true")
    g.add_argument("--file")
    g.add_argument("--text")
    p.add_argument("--section", help="append at the end of this heading's section")
    p.add_argument("--create", action="store_true", help="create the note or the section when missing")
    p.set_defaults(func=cmd_append)

    p = sub.add_parser("prop", help="show or change a note's properties")
    p.add_argument("note")
    p.add_argument("--set", action="append", default=[], metavar="KEY=VALUE")
    p.add_argument("--list", action="append", default=[], metavar="KEY=A,B")
    p.add_argument("--add", action="append", default=[], metavar="KEY=VALUE", help="add to a list (tags, aliases)")
    p.add_argument("--unset", action="append", default=[], metavar="KEY")
    p.set_defaults(func=cmd_prop)

    p = sub.add_parser("sync", help="run the vault's sync plugin in Obsidian and wait for commit and push")
    p.add_argument("--timeout", type=int)
    p.add_argument("--no-wait", action="store_true")
    p.set_defaults(func=cmd_sync)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except VaultError as exc:
        print(f"vault: {exc}", file=sys.stderr)
        return exc.code
    except (OSError, ValueError) as exc:
        print(f"vault: {getattr(exc, 'strerror', None) or exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
