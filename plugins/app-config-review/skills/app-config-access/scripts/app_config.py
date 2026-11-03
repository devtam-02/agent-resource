#!/usr/bin/env python3
"""Read a service's configuration from the Git config repositories (read-only).

Layout: <repo>/<service>/<environment>/<files>. Settings come from dev-cli's file
~/.dev-cli/.env (a process environment variable of the same name wins):

    APP_CONFIG_STAGING_REPO   repository holding <service>/staging
    APP_CONFIG_REPO           repository holding every other environment (uat, production, ...)
    APP_CONFIG_BRANCH         branch of both repositories, default master
    APP_CONFIG_CACHE          optional, cache folder of the shallow clones

    app_config.py doctor
    app_config.py list [--env staging] [--filter TEXT]
    app_config.py envs SERVICE
    app_config.py show SERVICE --env ENV [--file NAME] [--keys-only] [--json]
    app_config.py path SERVICE --env ENV

Secret values are printed as <secret>; credentials inside URLs are masked. The clones are
cache copies: nothing is committed or pushed. Also imported as a library by the
app-config-review skill. Python 3 standard library and git only.
"""

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit

ENV_STAGING, ENV_MAIN, ENV_BRANCH = "APP_CONFIG_STAGING_REPO", "APP_CONFIG_REPO", "APP_CONFIG_BRANCH"
STAGING = "staging"
SERVICE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
ENV_RE = re.compile(r"^[a-z][a-z0-9-]*$")
BRANCH_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]*$")
CONFIG_SUFFIXES = (".properties", ".yml", ".yaml", ".env")
MAX_FILE = 2_000_000

SECRET_KEY = re.compile(
    r"(passw(or)?d|passwd|pwd|secret|token|private[-_.]?key|api[-_.]?key|access[-_.]?key|"
    r"credential|signing[-_.]?key|jwt[-_.]?key|salt|client[-_.]?secret|auth[-_.]?key)", re.I)
NOT_SECRET_KEY = re.compile(
    r"(public[-_.]?key|key[-_.]?(id|name|alias|store[-_.]?type|path|location|file)|"
    r"token[-_.]?(ttl|expir\w*|validity|timeout|type|header|prefix|url|uri|endpoint|enabled|length|name)|"
    r"(expir\w*|ttl|timeout|enabled|length|header|url|uri|endpoint|max|min)[-_.]token|"
    r"secret[-_.]?(name|ref|id|enabled)|password[-_.]?(policy|min|max|pattern|regex|length|enabled|encoder|expir\w*)|"
    r"\.(enabled|enable)$)", re.I)
PLACEHOLDER = re.compile(r"^\$\{[^}]*\}$")
SAFE_SECRET = re.compile(r"^(?:ENC\(.*\)|\{cipher\}.*|vault:.*|(?:file|classpath|secret|ref|env):.*|<[^>]*>|\*+|changeme|)$", re.I)


# ---------------------------------------------------------------- parsing

def _strip_inline_comment(value):
    in_s = in_d = False
    for i, ch in enumerate(value):
        if ch == "'" and not in_d:
            in_s = not in_s
        elif ch == '"' and not in_s:
            in_d = not in_d
        elif ch == "#" and not in_s and not in_d and (i == 0 or value[i - 1].isspace()):
            return value[:i].rstrip()
    return value.rstrip()


def _unquote(value):
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
        return value[1:-1]
    return value


def parse_properties(text):
    """-> (entries [(key, value, line)], problems [(line, message)])."""
    entries, problems = [], []
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        raw, start = lines[i], i + 1
        i += 1
        s = raw.lstrip()
        if not s or s[0] in "#!":
            continue
        while s.endswith("\\") and not s.endswith("\\\\") and i < len(lines):
            s = s[:-1] + lines[i].lstrip()
            i += 1
        m = re.match(r"([^=:\s]+)\s*[=:]?\s*(.*)$", s)
        if not m:
            problems.append((start, "line is not key=value"))
            continue
        entries.append((m.group(1), m.group(2).strip(), start))
    return entries, problems


def parse_yaml(text):
    """Small block-style YAML reader: maps, lists, scalars, several documents."""
    entries, problems = [], []
    stack = []  # (indent, path, kind)
    docs = 0
    for number, raw in enumerate(text.splitlines(), 1):
        if raw.strip() == "---":
            docs += 1
            stack = []
            continue
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        if "\t" in raw[: len(raw) - len(raw.lstrip())]:
            problems.append((number, "tab used for indentation"))
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        body = raw.strip()
        while stack and stack[-1][0] >= indent and not (body.startswith("- ") and stack[-1][2] == "list" and stack[-1][0] == indent):
            stack.pop()
        base = stack[-1][1] if stack else ""
        if body.startswith("- ") or body == "-":
            item = body[1:].strip()
            idx = _next_index(entries, base)
            path = f"{base}[{idx}]"
            if ":" in item and not item.startswith(("'", '"')) and not re.match(r"^[^\s:]+://", item):
                k, _, v = item.partition(":")
                v = _strip_inline_comment(v)
                if v.strip():
                    entries.append((f"{path}.{k.strip()}", _unquote(v), number))
                    stack.append((indent, path, "list"))
                else:
                    stack.append((indent, path, "list"))
                    stack.append((indent + 2, f"{path}.{k.strip()}", "map"))
            else:
                entries.append((path, _unquote(_strip_inline_comment(item)), number))
            continue
        k, sep, v = body.partition(":")
        if not sep or not k.strip():
            problems.append((number, "line is not 'key: value'"))
            continue
        key = k.strip().strip("'\"")
        path = f"{base}.{key}" if base else key
        v = _strip_inline_comment(v)
        if v.strip() in ("", "|", ">", "|-", ">-"):
            stack.append((indent, path, "map"))
            if v.strip() in ("|", ">", "|-", ">-"):
                entries.append((path, "<block text>", number))
        else:
            entries.append((path, _unquote(v), number))
    return entries, problems


def _next_index(entries, base):
    prefix = base + "["
    seen = {e[0][len(prefix):].split("]", 1)[0] for e in entries if e[0].startswith(prefix)}
    return len(seen)


def load_env_dir(directory):
    """-> files {name: {"entries": {key: (value, line)}, "dups": [...], "problems": [...]}}"""
    files = {}
    for path in sorted(directory.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        name = path.relative_to(directory).as_posix()
        if not name.lower().endswith(CONFIG_SUFFIXES):
            continue
        if path.stat().st_size > MAX_FILE:
            files[name] = {"entries": {}, "dups": [], "problems": [(1, "file too large, skipped")]}
            continue
        try:
            text = path.read_text(encoding="utf-8-sig")
        except UnicodeDecodeError:
            files[name] = {"entries": {}, "dups": [], "problems": [(1, "not UTF-8, skipped")]}
            continue
        parser = parse_yaml if name.lower().endswith((".yml", ".yaml")) else parse_properties
        entries, problems = parser(text)
        seen, dups = {}, []
        for key, value, line in entries:
            if key in seen:
                dups.append((key, seen[key][1], line))
            seen[key] = (value, line)
        files[name] = {"entries": seen, "dups": dups, "problems": problems}
    return files


# ---------------------------------------------------------------- secrets and masking

def is_secret_key(key):
    return bool(SECRET_KEY.search(key)) and not NOT_SECRET_KEY.search(key)


def is_plain_secret(key, value):
    """A secret key whose value is a literal, not a placeholder or an encrypted token."""
    if not is_secret_key(key):
        return False
    v = value.strip()
    m = re.match(r"^\$\{[^:}]+:(.*)\}$", v)
    if m:  # ${NAME:default}: the default is a literal that ends up in the repository
        v = m.group(1)
        if v == "":
            return False
    elif PLACEHOLDER.match(v):
        return False
    if SAFE_SECRET.match(v):
        return False
    if re.fullmatch(r"(true|false|\d+|[A-Z_]+)", v, re.I) and not re.search(r"pass|secret", v, re.I):
        return False
    return True


def mask(value):
    """Mask credentials inside URLs and shorten."""
    value = re.sub(r"(://[^/\s:@]+:)[^/\s@]+@", r"\1***@", value)
    value = re.sub(r"([?&;](?:password|pwd|secret|token|key)=)[^&;\s]+", r"\1***", value, flags=re.I)
    return value if len(value) <= 90 else value[:87] + "..."


def shown(key, value):
    return "<secret>" if is_secret_key(key) else mask(value)


# ---------------------------------------------------------------- settings
# Settings live in dev-cli's file ~/.dev-cli/.env ($DEV_CLI_HOME moves the folder),
# shared by dev-cli and every agent-resource plugin. A process environment variable
# with the same name wins, for one-off runs. Same parsing rules as dev-cli: KEY=value,
# full-line # comments, optional single or double quotes, no other processing.

def dev_cli_env_path():
    home = os.environ.get("DEV_CLI_HOME", "").strip()
    return Path(home).expanduser() / ".env" if home else Path.home() / ".dev-cli" / ".env"


def _read_dev_cli_env():
    try:
        text = dev_cli_env_path().read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return {}
    values = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            quote, value = value[0], value[1:-1]
            if quote == '"':
                value = value.replace('\\"', '"').replace("\\\\", "\\")
        if key.strip():
            values[key.strip()] = value
    return values


_SETTINGS = None


def setting(name, default=""):
    """-> (value, source) with source "env", "file" or "default"."""
    global _SETTINGS
    if os.environ.get(name, "").strip():
        return os.environ[name].strip(), "env"
    if _SETTINGS is None:
        _SETTINGS = _read_dev_cli_env()
    if _SETTINGS.get(name, "").strip():
        return _SETTINGS[name].strip(), "file"
    return default, "default"


# ---------------------------------------------------------------- repositories

def repo_url(var):
    url, _ = setting(var)
    if not url:
        return None, f"{var} is not set in {dev_cli_env_path()}"
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https", "ssh", "git", "file") and not re.match(r"^[\w.-]+@[\w.-]+:", url):
        return None, f"{var}: unsupported URL scheme"
    if parts.password or (parts.username and parts.scheme in ("http", "https")):
        return None, f"{var}: remove the user/password from the URL; use a git credential helper or SSH key"
    return url, None


def cache_root():
    base = setting("APP_CONFIG_CACHE")[0] or os.path.join(os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache"), "app-config-review")
    return Path(base)


def git(args, cwd=None, timeout=300):
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0", GIT_ASKPASS="true", LC_ALL="C")
    p = subprocess.run(["git", *args], cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout)
    return p.returncode, (p.stdout + p.stderr).strip()


def sync(url, ref, fetch=True):
    """Shallow clone or update the cache clone of url; -> (path, commit, error)."""
    path = cache_root() / hashlib.sha1(f"{url}\n{ref}".encode()).hexdigest()[:12]
    try:
        if not (path / ".git").is_dir():
            path.parent.mkdir(parents=True, exist_ok=True)
            code, out = git(["clone", "--quiet", "--depth", "1", "--single-branch", "--branch", ref, "--", url, str(path)])
            if code:
                return None, None, _git_error(out)
        elif fetch:
            code, out = git(["fetch", "--quiet", "--depth", "1", "origin", ref], cwd=path)
            if code:
                return None, None, _git_error(out)
            code, out = git(["reset", "--quiet", "--hard", "FETCH_HEAD"], cwd=path)
            if code:
                return None, None, _git_error(out)
        code, head = git(["rev-parse", "--short", "HEAD"], cwd=path)
        return path, head if code == 0 else "?", None
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, None, f"git failed: {exc.__class__.__name__}"


def _git_error(out):
    out = re.sub(r"(https?://)[^/\s@]+@", r"\1***@", out)
    last = [l for l in out.splitlines() if l.strip()][-1:] or ["git failed"]
    if re.search(r"401|403|denied|authentication|could not read", out, re.I):
        return f"no access to the repository (check your git credentials): {last[0]}"
    return last[0]


def env_dir(path, service, env):
    d = (path / service / env).resolve()
    if path.resolve() not in d.parents:
        return None
    return d if d.is_dir() else None


def config_branch():
    b = setting(ENV_BRANCH, "master")[0] or "master"
    if not BRANCH_RE.match(b) or ".." in b:
        raise SystemExit(f"{ENV_BRANCH}: invalid branch name")
    return b


def repo_var_for(env):
    """Staging lives in its own repository; every other environment in the main one."""
    return ENV_STAGING if env == STAGING else ENV_MAIN


class ConfigRepoError(Exception):
    def __init__(self, message, code):
        super().__init__(message)
        self.code = code


def open_repo(var, fetch=True):
    """-> (path, commit) of the cache clone, or raise ConfigRepoError."""
    url, err = repo_url(var)
    if err:
        raise ConfigRepoError(f"{err}. See the plugin README (Setup).", 3)
    path, commit, err = sync(url, config_branch(), fetch)
    if err:
        raise ConfigRepoError(err, 5)
    return path, commit


def check_service(service):
    if not SERVICE_RE.match(service or ""):
        raise ConfigRepoError("invalid service name", 2)


def check_env(env):
    if not ENV_RE.match(env or ""):
        raise ConfigRepoError(f"invalid environment {env!r}", 2)


def services_with(path, env=None):
    out = []
    for p in sorted(path.iterdir()):
        if not p.is_dir() or p.name.startswith((".", "_")):
            continue
        if env is None or (p / env).is_dir():
            out.append(p.name)
    return out


def load_service_env(service, env, fetch=True, opened=None):
    """-> dict(files=..., dir=Path, commit=str, repo=var) for <service>/<env>; raise ConfigRepoError.

    `opened` caches open_repo results per repository variable within one run.
    """
    check_service(service)
    check_env(env)
    var = repo_var_for(env)
    if opened is not None and var in opened:
        path, commit = opened[var]
    else:
        path, commit = open_repo(var, fetch)
        if opened is not None:
            opened[var] = (path, commit)
    d = env_dir(path, service, env)
    if d is None:
        near = [n for n in services_with(path, env) if service.lower() in n.lower() or n.lower() in service.lower()][:8]
        hint = f"; similar: {', '.join(near)}" if near else ""
        raise ConfigRepoError(f"{service}/{env} not found in {var}{hint}", 4)
    return {"files": load_env_dir(d), "dir": d, "commit": commit, "repo": var}


# ---------------------------------------------------------------- commands

def _fail(exc):
    print(f"error: {exc}", file=sys.stderr)
    return exc.code


def cmd_doctor(args):
    ok = True
    found = dev_cli_env_path().is_file()
    print(f"settings file: {dev_cli_env_path()} ({'found' if found else 'MISSING: run dev-cli once to create it'})")
    for var in (ENV_STAGING, ENV_MAIN):
        url, err = repo_url(var)
        source = setting(var)[1]
        print(f"{var}: " + (f"set ({'environment variable' if source == 'env' else 'settings file'})" if url else f"MISSING ({err})"))
        ok &= url is not None
    print(f"{ENV_BRANCH}: {config_branch()}")
    print(f"cache: {cache_root()}")
    for var in (ENV_STAGING, ENV_MAIN):
        url, _ = repo_url(var)
        if url:
            code, out = git(["ls-remote", "--heads", url, config_branch()], timeout=60)
            reachable = code == 0 and bool(out)
            print(f"{var}: " + ("reachable, branch found" if reachable else
                                ("NOT reachable: " + _git_error(out)) if code else "branch not found"))
            ok &= reachable
    return 0 if ok else 3


def cmd_list(args):
    try:
        if args.env:
            check_env(args.env)
        var = repo_var_for(args.env or STAGING)
        path, commit = open_repo(var, not args.no_fetch)
    except ConfigRepoError as exc:
        return _fail(exc)
    names = [s for s in services_with(path, args.env) if not args.filter or args.filter.lower() in s.lower()]
    for n in names:
        print(n)
    where = f"with a {args.env}/ folder " if args.env else ""
    print(f"{len(names)} service(s) {where}in {var} (commit {commit})", file=sys.stderr)
    return 0


def cmd_envs(args):
    try:
        check_service(args.service)
        rows = []
        for var in (ENV_STAGING, ENV_MAIN):
            path, commit = open_repo(var, not args.no_fetch)
            d = path / args.service
            if d.is_dir():
                for e in sorted(p.name for p in d.iterdir() if p.is_dir() and not p.name.startswith(".")):
                    rows.append((e, var, repo_var_for(e) == var))
    except ConfigRepoError as exc:
        return _fail(exc)
    if not rows:
        print(f"error: {args.service} not found in either repository", file=sys.stderr)
        return 4
    for env, var, used in rows:
        print(f"{env:16} {var}" + ("" if used else "   (not used: this environment is read from the other repository)"))
    return 0


def cmd_show(args):
    try:
        data = load_service_env(args.service, args.env, not args.no_fetch)
    except ConfigRepoError as exc:
        return _fail(exc)
    files = data["files"]
    if args.file:
        if args.file not in files:
            print(f"error: no {args.file} in {args.service}/{args.env}; files: {', '.join(files) or '(none)'}", file=sys.stderr)
            return 4
        files = {args.file: files[args.file]}
    if args.json:
        print(json.dumps({
            "service": args.service, "environment": args.env, "repository": data["repo"], "commit": data["commit"],
            "files": {name: {key: (None if args.keys_only else shown(key, value)) for key, (value, _) in f["entries"].items()}
                      for name, f in files.items()},
            "other_files": sorted(p.relative_to(data["dir"]).as_posix() for p in data["dir"].rglob("*")
                                  if p.is_file() and p.relative_to(data["dir"]).as_posix() not in data["files"]),
        }, ensure_ascii=False, indent=2))
        return 0
    print(f"# {args.service} / {args.env}  ({data['repo']}, commit {data['commit']})")
    for name, f in files.items():
        print(f"\n## {name}")
        for line, msg in f["problems"]:
            print(f"# line {line}: {msg}")
        for key, (value, _) in f["entries"].items():
            print(key if args.keys_only else f"{key}={shown(key, value)}")
    return 0


def cmd_path(args):
    try:
        data = load_service_env(args.service, args.env, not args.no_fetch)
    except ConfigRepoError as exc:
        return _fail(exc)
    print(data["dir"])
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("doctor", help="check the settings and repository access").set_defaults(fn=cmd_doctor)
    p = sub.add_parser("list", help="services (that have an ENV folder)")
    p.add_argument("--env")
    p.add_argument("--filter")
    p.set_defaults(fn=cmd_list)
    p = sub.add_parser("envs", help="environments of one service and their repository")
    p.add_argument("service")
    p.set_defaults(fn=cmd_envs)
    p = sub.add_parser("show", help="configuration of SERVICE/ENV, secrets masked")
    p.add_argument("service")
    p.add_argument("--env", required=True)
    p.add_argument("--file")
    p.add_argument("--keys-only", action="store_true")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_show)
    p = sub.add_parser("path", help="local folder of SERVICE/ENV in the cache clone")
    p.add_argument("service")
    p.add_argument("--env", required=True)
    p.set_defaults(fn=cmd_path)
    for s in sub.choices.values():
        s.add_argument("--no-fetch", action="store_true", help="use the cached clone as it is")
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
