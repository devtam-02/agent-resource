#!/usr/bin/env python3
"""Read-only review of one service's configuration kept in Git config repositories.

Layout: <repo>/<service>/<environment>/<files>. Staging lives in one repository, UAT and
production in another. Repository URLs come from the environment, never from this file:

    APP_CONFIG_STAGING_REPO   repository holding <service>/staging
    APP_CONFIG_REPO           repository holding <service>/uat and <service>/production
    APP_CONFIG_BRANCH         branch, default master (applies to both)

    config_review.py list [--filter TEXT]
    config_review.py review SERVICE [--compare uat,production] [--json] [--no-fetch]
    config_review.py doctor

Values of secrets are never printed: findings name the key and the file only. Other values are
shown only where a difference needs them, with credentials in URLs masked. Repositories are
cloned shallow into a cache folder and only read; nothing is ever committed or pushed.
Python 3 standard library and git only.
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
REVIEWED = "staging"
SERVICE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
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
ENV_SPECIFIC_KEY = re.compile(
    r"(url|uri|host|hostname|address|endpoint|server|broker|cluster|bootstrap|nodes|domain|origin|"
    r"username|user|port|path|dir|topic|queue|group|namespace|schema|database|bucket|realm|issuer|"
    r"client[-_.]?id|instance|context|zone|region|cors|redirect|callback|base[-_.]?path)", re.I)
URL_KEY = re.compile(r"(url|uri|host|hostname|address|endpoint|server|broker|nodes|bootstrap)", re.I)
URL_LIKE = re.compile(r"^(?:[a-z][a-z0-9+.-]*:)?//|^(?:jdbc|redis|rediss|mongodb(?:\+srv)?|amqp|kafka)[a-z0-9+:.-]*:", re.I)
HOSTLIKE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.-]*:\d{2,5}(?:[,/].*)?$|^[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+(?::\d+)?$")
PLACEHOLDER = re.compile(r"^\$\{[^}]*\}$")
SAFE_SECRET = re.compile(r"^(?:ENC\(.*\)|\{cipher\}.*|vault:.*|(?:file|classpath|secret|ref|env):.*|<[^>]*>|\*+|changeme|)$", re.I)
CRED_IN_URL = re.compile(r"://[^/\s:@]+:[^/\s@]+@")
PROD_MARK = re.compile(r"(?<![a-z])(prod(?:uction)?|prd)(?![a-z])", re.I)
OTHER_ENV_MARK = re.compile(r"(?<![a-z])(uat|prod(?:uction)?|prd|dr)(?![a-z])", re.I)
LOCAL_HOST = re.compile(r"(?<![\w.-])(localhost|127\.0\.0\.1|0\.0\.0\.0|::1)(?![\w.-])")

RISKY = [
    # (id, level, key regex, value test, message)
    ("ddl-auto", "error", re.compile(r"hibernate\.ddl-auto$|jpa\.hibernate\.ddl-auto$", re.I),
     lambda v: v.lower() in ("create", "create-drop", "update"), "schema is changed by Hibernate at start-up; use validate/none and Liquibase/Flyway"),
    ("show-sql", "warn", re.compile(r"show[-_.]?sql$", re.I), lambda v: v.lower() == "true", "SQL printed to the log"),
    ("debug-flag", "warn", re.compile(r"^debug$", re.I), lambda v: v.lower() == "true", "Spring debug mode on"),
    ("root-log-level", "warn", re.compile(r"^logging\.level\.root$", re.I), lambda v: v.upper() in ("DEBUG", "TRACE"),
     "root log level is DEBUG/TRACE: noisy and may log personal or money data"),
    ("actuator-exposure", "error", re.compile(r"management\.endpoints?\.web\.exposure\.include$", re.I),
     lambda v: v.strip() in ("*", '"*"', "'*'"), "every actuator endpoint is exposed (env, heapdump, ...)"),
    ("actuator-env", "error", re.compile(r"management\.endpoint\.(env|heapdump|threaddump|configprops)\.enabled$", re.I),
     lambda v: v.lower() == "true", "sensitive actuator endpoint enabled"),
    ("auth-disabled", "error", re.compile(r"(^|\.)(auth|authentication|security|authorization)(\.[a-z-]+)?\.enabled$", re.I),
     lambda v: v.lower() == "false", "authentication/security switched off"),
    ("ssl-verify-off", "error", re.compile(r"(ssl|tls|https?)\.?.*(verify|validate|check)[-_.]?(hostname|cert\w*|ssl)?$|trust[-_.]?all|insecure", re.I),
     lambda v: False, "TLS verification disabled or insecure transport allowed"),
    ("swagger", "warn", re.compile(r"(springdoc|swagger)[a-z.-]*\.enabled$", re.I), lambda v: v.lower() == "true",
     "API docs enabled (fine on staging; make sure UAT/production differ on purpose)"),
    ("cors-any", "warn", re.compile(r"cors.*allowed[-_.]?origins?$", re.I), lambda v: v.strip() in ("*", '"*"'), "CORS allows any origin"),
    ("h2-console", "error", re.compile(r"h2\.console\.enabled$", re.I), lambda v: v.lower() == "true", "H2 console enabled"),
]
TIMEOUT_KEY = re.compile(r"(timeout|time[-_.]out)", re.I)
POOL_KEY = re.compile(r"(max[-_.]?(total|pool[-_.]?size|per[-_.]?route|active|connections)|pool[-_.]?size)", re.I)


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


# ---------------------------------------------------------------- rules

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


def endpoint_value(key, value):
    v = value.strip()
    if not v or PLACEHOLDER.match(v):
        return False
    if URL_LIKE.match(v) and not LOCAL_HOST.search(v):
        return True
    return bool(URL_KEY.search(key)) and bool(HOSTLIKE.match(v))


PRIVATE_HOST = re.compile(r"(?<![\d.])(10(?:\.\d{1,3}){3}|192\.168(?:\.\d{1,3}){2}|172\.(?:1[6-9]|2\d|3[01])(?:\.\d{1,3}){2})(?![\d.])")


def private_target(value):
    """True for a private IP address or a host name without a public-looking domain."""
    if PRIVATE_HOST.search(value):
        return True
    host = re.sub(r"^[a-z][a-z0-9+.-]*:(//)?", "", value.strip(), flags=re.I)
    host = re.sub(r"^[a-z][a-z0-9+.-]*://", "", host, flags=re.I).split("/")[0].split(":")[0].split("@")[-1]
    return bool(host) and "." not in host


def finding(level, rule, file, key, message, line=None, **extra):
    d = {"level": level, "rule": rule, "file": file, "key": key, "message": message}
    if line:
        d["line"] = line
    d.update(extra)
    return d


def review_staging(files, other):
    """Rules on the reviewed environment; `other` = {env: files} for cross-environment rules."""
    out = []
    for name, f in files.items():
        for line, msg in f["problems"]:
            out.append(finding("warn", "parse", name, "", msg, line))
        for key, first, second in f["dups"]:
            out.append(finding("warn", "duplicate-key", name, key, f"defined twice (lines {first} and {second}); the last one wins", second))
        timeouts_seen = any(TIMEOUT_KEY.search(k) for k in f["entries"])
        for key, (value, line) in f["entries"].items():
            v = value.strip()
            if CRED_IN_URL.search(v):
                out.append(finding("error", "credential-in-url", name, key, "user and password inside a URL; use separate secret keys", line))
            elif is_plain_secret(key, v):
                out.append(finding("error", "plain-secret", name, key,
                                   "secret stored as plain text; use a placeholder, ENC(...) or the secret store", line))
            for rid, level, key_rx, test, msg in RISKY:
                if key_rx.search(key):
                    try:
                        hit = bool(test(v))
                    except Exception:
                        hit = False
                    if rid == "ssl-verify-off":
                        low = key.lower()
                        hit = (("verify" in low or "validate" in low or "check" in low) and v.lower() == "false" and
                               any(w in low for w in ("ssl", "tls", "cert", "hostname"))) or \
                              ("trust-all" in low or "trustall" in low or "insecure" in low) and v.lower() == "true"
                    if hit:
                        out.append(finding(level, rid, name, key, msg, line))
            if URL_KEY.search(key) or URL_LIKE.match(v):
                if LOCAL_HOST.search(v) and not is_secret_key(key):
                    out.append(finding("warn", "localhost", name, key, "points at localhost: unreachable from a container or another host", line))
                other_marks = {m.group(1).lower() for m in OTHER_ENV_MARK.finditer(re.sub(r"\$\{[^}]*\}", "", v))}
                if other_marks and endpoint_value(key, v):
                    out.append(finding("error" if other_marks & {"prod", "production", "prd"} else "warn", "other-env-marker", name, key,
                                       f"staging value mentions {', '.join(sorted(other_marks))}: {mask(v)}", line))
            if TIMEOUT_KEY.search(key) and re.fullmatch(r"-?\d+", v) and int(v) <= 0:
                out.append(finding("warn", "timeout-zero", name, key, "timeout 0 or negative means wait forever", line))
            if POOL_KEY.search(key) and re.fullmatch(r"\d+", v) and int(v) > 500:
                out.append(finding("warn", "pool-huge", name, key, f"pool/connection limit {v} is very high", line))
        if f["entries"] and (name.lower().startswith("application") or name.lower().startswith("bootstrap")):
            has_http = any(k.startswith(("rest.", "feign.", "webclient.", "app.rest.")) or "http-client" in k for k in f["entries"])
            if has_http and not timeouts_seen:
                out.append(finding("warn", "no-timeout", name, "", "HTTP client settings without any timeout key"))
    # cross-environment: same value as another environment
    for env, other_files in other.items():
        for name, f in files.items():
            g = other_files.get(name)
            if not g:
                continue
            for key, (value, line) in f["entries"].items():
                if key not in g["entries"]:
                    continue
                ov = g["entries"][key][0]
                if value.strip() != ov.strip() or not value.strip() or PLACEHOLDER.match(value.strip()):
                    continue
                if is_secret_key(key) and is_plain_secret(key, value):
                    out.append(finding("error", "secret-shared", name, key, f"same secret value as {env}", line, other=env))
                elif endpoint_value(key, value) and env in ("production", "prod") and private_target(value):
                    out.append(finding("error", "points-at-production", name, key,
                                       f"internal address identical to the {env} value: {mask(value)}", line, other=env))
                elif endpoint_value(key, value):
                    out.append(finding("warn", "same-endpoint", name, key,
                                       f"identical to the {env} value: {mask(value)}", line, other=env))
    return out


def drift(files, env, other_files):
    """Key and value differences between the reviewed environment and another one."""
    out = []
    for name in sorted(set(files) | set(other_files)):
        a, b = files.get(name), other_files.get(name)
        if a is None:
            out.append(finding("warn", "file-missing", name, "", f"present in {env}, absent from {REVIEWED}"))
            continue
        if b is None:
            out.append(finding("info", "file-only-here", name, "", f"present in {REVIEWED}, absent from {env}"))
            continue
        ka, kb = set(a["entries"]), set(b["entries"])
        for key in sorted(kb - ka):
            out.append(finding("warn", "key-missing", name, key, f"set in {env}, missing in {REVIEWED}", env=env))
        extra = sorted(ka - kb)
        if extra:
            more = f" (+{len(extra) - 8} more)" if len(extra) > 8 else ""
            out.append(finding("info", "key-extra", name, "", f"{len(extra)} key(s) only in {REVIEWED}, not in {env}: "
                               + ", ".join(extra[:8]) + more, env=env, keys=extra))
        for key in sorted(ka & kb):
            va, vb = a["entries"][key][0].strip(), b["entries"][key][0].strip()
            if va == vb or is_secret_key(key) or (ENV_SPECIFIC_KEY.search(key) and not re.search(r"(enabled|timeout|size|max|min|level|ttl)", key, re.I)):
                continue
            out.append(finding("info", "value-differs", name, key, f"{REVIEWED}={shown(key, va)}  {env}={shown(key, vb)}", env=env))
    return out


# ---------------------------------------------------------------- repositories

def repo_url(var):
    url = os.environ.get(var, "").strip()
    if not url:
        return None, f"{var} is not set"
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https", "ssh", "git", "file") and not re.match(r"^[\w.-]+@[\w.-]+:", url):
        return None, f"{var}: unsupported URL scheme"
    if parts.password or (parts.username and parts.scheme in ("http", "https")):
        return None, f"{var}: remove the user/password from the URL; use a git credential helper or SSH key"
    return url, None


def branch():
    b = os.environ.get(ENV_BRANCH, "master").strip() or "master"
    if not BRANCH_RE.match(b) or ".." in b:
        raise SystemExit(f"{ENV_BRANCH}: invalid branch name")
    return b


def cache_root():
    base = os.environ.get("APP_CONFIG_CACHE") or os.path.join(os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache"), "app-config-review")
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


# ---------------------------------------------------------------- commands

def cmd_doctor(args):
    ok = True
    for var in (ENV_STAGING, ENV_MAIN):
        url, err = repo_url(var)
        print(f"{var}: {'set' if url else 'MISSING'}" + (f" ({err})" if err and url is None and os.environ.get(var) else ""))
        ok &= url is not None
    print(f"{ENV_BRANCH}: {branch()}")
    print(f"cache: {cache_root()}")
    for var in (ENV_STAGING, ENV_MAIN):
        url, _ = repo_url(var)
        if url:
            code, out = git(["ls-remote", "--heads", url, branch()], timeout=60)
            print(f"{var}: {'reachable, branch found' if code == 0 and out else 'NOT reachable or branch missing: ' + _git_error(out) if code else 'branch not found'}")
            ok &= code == 0 and bool(out)
    return 0 if ok else 3


def need(var):
    url, err = repo_url(var)
    if err:
        print(f"error: {err}. See the plugin README (Setup).", file=sys.stderr)
        raise SystemExit(3)
    return url


def cmd_list(args):
    path, commit, err = sync(need(ENV_STAGING), branch(), not args.no_fetch)
    if err:
        print(f"error: {err}", file=sys.stderr)
        return 5
    names = [s for s in sorted(p.name for p in path.iterdir() if p.is_dir() and (p / REVIEWED).is_dir())
             if not args.filter or args.filter.lower() in s.lower()]
    for n in names:
        print(n)
    print(f"{len(names)} service(s) with a {REVIEWED}/ folder (commit {commit})", file=sys.stderr)
    return 0


def cmd_review(args):
    service = args.service
    if not SERVICE_RE.match(service) or service.startswith("."):
        print("error: invalid service name", file=sys.stderr)
        return 2
    compare = [e.strip() for e in args.compare.split(",") if e.strip()]
    bad = [e for e in compare if not re.fullmatch(r"[a-z][a-z0-9-]*", e) or e == REVIEWED]
    if bad:
        print(f"error: invalid environment {bad[0]!r}", file=sys.stderr)
        return 2
    staging_repo = need(ENV_STAGING)
    ref = branch()
    path, commit_staging, err = sync(staging_repo, ref, not args.no_fetch)
    if err:
        print(f"error: {err}", file=sys.stderr)
        return 5
    sdir = env_dir(path, service, REVIEWED)
    if sdir is None:
        near = [p.name for p in path.iterdir() if p.is_dir() and service.lower() in p.name.lower()][:8]
        print(f"error: {service}/{REVIEWED} not found in the staging repository" + (f"; similar: {', '.join(near)}" if near else ""), file=sys.stderr)
        return 4
    files = load_env_dir(sdir)
    others, commits, notes = {}, {"staging": commit_staging}, []
    if compare:
        main_path, commit_main, err = sync(need(ENV_MAIN), ref, not args.no_fetch)
        if err:
            notes.append(f"comparison skipped: {err}")
        else:
            commits["main"] = commit_main
            for env in compare:
                d = env_dir(main_path, service, env)
                if d is None:
                    notes.append(f"no {service}/{env} folder in the main config repository: {env} not compared")
                else:
                    others[env] = load_env_dir(d)
    findings = review_staging(files, others)
    for env, other_files in others.items():
        findings.extend(drift(files, env, other_files))
    order = {"error": 0, "warn": 1, "info": 2}
    findings.sort(key=lambda f: (order[f["level"]], f["rule"], f["file"], f["key"]))
    counts = {lvl: sum(1 for f in findings if f["level"] == lvl) for lvl in order}
    report = {"service": service, "environment": REVIEWED, "branch": ref, "commits": commits,
              "files": sorted(files), "compared": sorted(others), "notes": notes, "counts": counts, "findings": findings}
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"{service} / {REVIEWED}  (branch {ref}, commits {commits})")
        print(f"files: {', '.join(sorted(files)) or '(none)'}")
        print(f"compared with: {', '.join(sorted(others)) or '(nothing)'}")
        for n in notes:
            print(f"note: {n}")
        for f in findings:
            where = f["file"] + (f":{f['line']}" if f.get("line") else "")
            key = f" {f['key']}" if f["key"] else ""
            print(f"{f['level']:5} {f['rule']:20} {where}{key}: {f['message']}")
        print(f"{counts['error']} error(s), {counts['warn']} warning(s), {counts['info']} info")
    return 1 if counts["error"] else 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("doctor", help="check the environment variables and repository access").set_defaults(fn=cmd_doctor)
    p = sub.add_parser("list", help="services that have a staging folder")
    p.add_argument("--filter")
    p.add_argument("--no-fetch", action="store_true", help="use the cached clone as it is")
    p.set_defaults(fn=cmd_list)
    p = sub.add_parser("review", help="review one service")
    p.add_argument("service")
    p.add_argument("--compare", default="uat,production", help="environments to compare with, '' for none")
    p.add_argument("--json", action="store_true")
    p.add_argument("--no-fetch", action="store_true")
    p.set_defaults(fn=cmd_review)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
