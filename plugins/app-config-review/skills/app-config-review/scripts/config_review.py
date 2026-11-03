#!/usr/bin/env python3
"""Review the configuration of one environment against the code that will run with it (read-only).

Run it inside a code repository. The configuration comes from the Git config repositories
(through the app-config-access skill's app_config.py); the code from a branch of this repository:

    staging -> branch staging, uat -> branch release   (APP_CONFIG_CODE_BRANCHES in ~/.dev-cli/.env)
    any environment: --code current (working tree, uncommitted changes included) or --code <branch>

    config_review.py --env staging
    config_review.py --env uat [--compare production] [--json]
    config_review.py --env production --code master
    config_review.py --env staging --code current --service my-service
    config_review.py --env staging --no-code      (configuration rules only)

The branch is read from git objects (git fetch of that one branch, then ls-tree / cat-file): the
working tree and the checked-out branch are never touched. Secret values are never printed.
Exit codes: 0 no error, 1 errors found, 2 bad arguments, 3 not configured, 4 not found, 5 git.
Python 3 standard library and git only.
"""

import argparse
import difflib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

_ACCESS = Path(__file__).resolve().parents[2] / "app-config-access" / "scripts"
sys.path.insert(0, str(_ACCESS))
from app_config import (  # noqa: E402  (shared with the app-config-access skill)
    BRANCH_RE, MAX_FILE, ConfigRepoError, _git_error, check_env, git, is_plain_secret,
    is_secret_key, load_service_env, mask, open_repo, parse_properties, parse_yaml, repo_var_for,
    services_with, setting, shown,
)

ENV_SPECIFIC_KEY = re.compile(
    r"(url|uri|host|hostname|address|endpoint|server|broker|cluster|bootstrap|nodes|domain|origin|"
    r"username|user|port|path|dir|topic|queue|group|namespace|schema|database|bucket|realm|issuer|"
    r"client[-_.]?id|instance|context|zone|region|cors|redirect|callback|base[-_.]?path)", re.I)
URL_KEY = re.compile(r"(url|uri|host|hostname|address|endpoint|server|broker|nodes|bootstrap)", re.I)
URL_LIKE = re.compile(r"^(?:[a-z][a-z0-9+.-]*:)?//|^(?:jdbc|redis|rediss|mongodb(?:\+srv)?|amqp|kafka)[a-z0-9+:.-]*:", re.I)
HOSTLIKE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.-]*:\d{2,5}(?:[,/].*)?$|^[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+(?::\d+)?$")
PLACEHOLDER = re.compile(r"^\$\{[^}]*\}$")
CRED_IN_URL = re.compile(r"://[^/\s:@]+:[^/\s@]+@")
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



# ---------------------------------------------------------------- configuration rules

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


def review_config(files, other, reviewed):
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
                level, marks = foreign_markers(reviewed, v)
                if level and endpoint_value(key, v):
                    out.append(finding(level, "other-env-marker", name, key,
                                       f"{reviewed} value mentions {', '.join(sorted(marks))}: {mask(v)}", line))
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
                elif endpoint_value(key, value) and env_family(env) == "production" and reviewed != env and private_target(value):
                    out.append(finding("error", "points-at-production", name, key,
                                       f"internal address identical to the {env} value: {mask(value)}", line, other=env))
                elif endpoint_value(key, value):
                    out.append(finding("warn", "same-endpoint", name, key,
                                       f"identical to the {env} value: {mask(value)}", line, other=env))
    return out


def drift(files, env, other_files, reviewed):
    """Key and value differences between the reviewed environment and another one."""
    out = []
    for name in sorted(set(files) | set(other_files)):
        a, b = files.get(name), other_files.get(name)
        if a is None:
            out.append(finding("warn", "file-missing", name, "", f"present in {env}, absent from {reviewed}"))
            continue
        if b is None:
            out.append(finding("info", "file-only-here", name, "", f"present in {reviewed}, absent from {env}"))
            continue
        ka, kb = set(a["entries"]), set(b["entries"])
        for key in sorted(kb - ka):
            out.append(finding("warn", "key-missing", name, key, f"set in {env}, missing in {reviewed}", env=env))
        extra = sorted(ka - kb)
        if extra:
            more = f" (+{len(extra) - 8} more)" if len(extra) > 8 else ""
            out.append(finding("info", "key-extra", name, "", f"{len(extra)} key(s) only in {reviewed}, not in {env}: "
                               + ", ".join(extra[:8]) + more, env=env, keys=extra))
        for key in sorted(ka & kb):
            va, vb = a["entries"][key][0].strip(), b["entries"][key][0].strip()
            if va == vb or is_secret_key(key) or (ENV_SPECIFIC_KEY.search(key) and not re.search(r"(enabled|timeout|size|max|min|level|ttl)", key, re.I)):
                continue
            out.append(finding("info", "value-differs", name, key, f"{reviewed}={shown(key, va)}  {env}={shown(key, vb)}", env=env))
    return out




# ---------------------------------------------------------------- environment markers

ENV_WORDS = {
    "production": {"prod", "production", "prd", "live"},
    "uat": {"uat"},
    "staging": {"staging", "stg", "stag"},
    "dev": {"dev", "develop"},
}
MARK_RE = re.compile(r"(?<![a-z0-9])(prod|production|prd|live|uat|staging|stg|stag|dev|develop)(?![a-z0-9])", re.I)


def env_family(env):
    for family, words in ENV_WORDS.items():
        if env in words or env == family:
            return family
    return env


def foreign_markers(env, value):
    """-> (level, words) for words in a value that name another environment, or (None, set())."""
    mine = env_family(env)
    words = {m.group(1).lower() for m in MARK_RE.finditer(re.sub(r"\$\{[^}]*\}", "", value))}
    foreign = {w for w in words if env_family(w) != mine}
    if not foreign:
        return None, set()
    families = {env_family(w) for w in foreign}
    if mine != "production" and "production" in families:
        return "error", foreign
    if mine == "production" and families & {"staging", "dev", "uat"}:
        return "error", foreign
    if mine == "staging" and "dev" in families:
        return None, set()            # staging next to dev systems is common: not reported
    return "warn", foreign


# ---------------------------------------------------------------- code repository

FRAMEWORK_PREFIXES = (
    "spring.", "server.", "management.", "logging.", "info.", "eureka.", "feign.", "ribbon.", "hystrix.",
    "resilience4j.", "springdoc.", "swagger.", "jasypt.", "mybatis.", "mybatis-plus.", "debug", "trace",
    "zuul.", "kafka.", "camunda.", "opentracing.", "otel.", "jaeger.", "zipkin.", "sleuth.", "micrometer.",
    "redisson.", "seata.", "nacos.", "dubbo.", "grpc.", "graphql.", "keycloak.", "jwt.", "security.",
    "liquibase.", "flyway.", "hibernate.", "javax.", "jakarta.", "netty.", "reactor.", "endpoints.",
)
CODE_SUFFIXES = (".java", ".kt", ".groovy")
CONFIG_NAME = re.compile(r"(^|/)(application|bootstrap)(-[A-Za-z0-9_.-]+)?\.(properties|ya?ml)$")
SKIP_DIRS = {".git", "target", "build", "out", "node_modules", ".idea", ".gradle", ".mvn", "bin", "dist", ".vscode"}
GET_PROPERTY = re.compile(r"\b(getProperty|getRequiredProperty|containsProperty)\s*\(\s*\"([^\"]+)\"\s*(,[^;]*)?\)")
CONFIG_PROPS = re.compile(r"@ConfigurationProperties\s*\(\s*(?:(?:prefix|value)\s*=\s*)?\"([^\"]+)\"")
COND_PROP = re.compile(r"@ConditionalOnProperty\s*\(([^)]*)\)", re.S)
VALUE_JAVA = re.compile(
    r"@Value\s*\(\s*\"((?:[^\"\\]|\\.)*)\"\s*\)\s*(?:@\w+(?:\([^)]*\))?\s*)*"
    r"(?:(?:private|protected|public|final|static|transient|volatile)\s+)*"
    r"([A-Za-z_][\w.]*(?:<[^;=(){}]*>)?(?:\[\])*)\s+([A-Za-z_]\w*)\s*[;=,)]")
VALUE_KOTLIN = re.compile(
    r"@(?:field:|param:)?Value\s*\(\s*\"((?:[^\"\\]|\\.)*)\"\s*\)\s*(?:(?:private|protected|internal|lateinit|override)\s+)*"
    r"(?:val|var)\s+([A-Za-z_]\w*)\s*:\s*([\w.<>?]+)")
FIELD_JAVA = re.compile(r"^\s*(?:private|protected|public)\s+(?:final\s+)?[\w.<>, ?\[\]]+\s+([a-z]\w*)\s*[;=]", re.M)
ENV_VAR_KEY = re.compile(r"^[A-Z][A-Z0-9_]*$")
INT_TYPES = {"int", "integer", "long", "short", "byte", "java.lang.integer", "java.lang.long"}
FLOAT_TYPES = {"double", "float", "bigdecimal", "java.math.bigdecimal"}
BOOL_TYPES = {"boolean", "java.lang.boolean"}
KOTLIN_TYPES = {"Int": "int", "Long": "long", "Short": "short", "Double": "double", "Float": "float", "Boolean": "boolean",
                "Int?": "int", "Long?": "long", "Boolean?": "boolean", "Double?": "double", "Duration": "duration"}


def norm(key):
    """Spring relaxed binding, roughly: case, '-' and '_' do not matter; list indexes dropped."""
    return re.sub(r"\[\d+\]", "", key).replace("-", "").replace("_", "").lower()


def kebab(name):
    return re.sub(r"(?<!^)(?=[A-Z])", "-", name).lower()


def iter_placeholders(text):
    """Yield (key, default or None, offset) for every ${key[:default]} in text, defaults included."""
    i = 0
    while True:
        i = text.find("${", i)
        if i < 0:
            return
        depth, j = 0, i
        while j < len(text):
            if text.startswith("${", j):
                depth += 1
                j += 2
                continue
            if text[j] == "}":
                depth -= 1
                if depth == 0:
                    break
            if text[j] in "\n\"" and depth:
                break
            j += 1
        body = text[i + 2:j]
        if j >= len(text) or text[j] != "}":
            i += 2
            continue
        key, sep, default = body.partition(":")
        key = key.strip()
        if re.fullmatch(r"[A-Za-z0-9_.\-\[\]]+", key or ""):
            yield key, (default if sep else None), i
        if sep and "${" in default:
            for inner in iter_placeholders(default):
                yield inner[0], inner[1], i + inner[2]
        i = j + 1


def line_of(text, offset):
    return text.count("\n", 0, offset) + 1


class CodeSource:
    """Files of a code repository, from a commit (git objects, nothing checked out) or the working tree."""

    def __init__(self, root, commit=None):
        self.root, self.commit = Path(root), commit

    def paths(self):
        if self.commit:
            code, out = git(["ls-tree", "-r", "-z", "--name-only", self.commit], cwd=self.root, timeout=120)
            if code:
                raise ConfigRepoError(f"cannot list files of {self.commit}: {out[-200:]}", 5)
            return [p for p in out.split("\0") if p and not (set(p.split("/")[:-1]) & SKIP_DIRS)]
        out = []
        for cur, dirs, files in os.walk(self.root):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
            for f in files:
                out.append(Path(cur, f).relative_to(self.root).as_posix())
        return out

    def read_many(self, paths):
        """-> {path: text} (UTF-8, undecodable or huge files skipped)."""
        result = {}
        if not paths:
            return result
        if not self.commit:
            for p in paths:
                full = self.root / p
                try:
                    if full.stat().st_size <= MAX_FILE:
                        result[p] = full.read_text(encoding="utf-8-sig")
                except (OSError, UnicodeDecodeError):
                    pass
            return result
        env = dict(os.environ, GIT_TERMINAL_PROMPT="0", LC_ALL="C")
        payload = "".join(f"{self.commit}:{p}\n" for p in paths if "\n" not in p).encode()
        proc = subprocess.run(["git", "cat-file", "--batch"], cwd=self.root, input=payload, capture_output=True, env=env, timeout=300)
        data, pos = proc.stdout, 0
        for p in [p for p in paths if "\n" not in p]:
            nl = data.find(b"\n", pos)
            if nl < 0:
                break
            header = data[pos:nl].split()
            pos = nl + 1
            if len(header) < 3 or header[1] != b"blob":
                continue
            size = int(header[2])
            blob = data[pos:pos + size]
            pos += size + 1
            if size <= MAX_FILE:
                try:
                    result[p] = blob.decode("utf-8-sig")
                except UnicodeDecodeError:
                    pass
        return result


def resolve_code(repo, env, code, fetch):
    """-> (CodeSource, description dict). code: None (mapping), "current", or a branch name."""
    code_root = Path(repo).resolve()
    rc, top = git(["rev-parse", "--show-toplevel"], cwd=code_root, timeout=30)
    is_git = rc == 0
    if is_git:
        code_root = Path(top)
    if code is None:
        mapping = branch_mapping()
        if env not in mapping:
            known = ", ".join(f"{e}->{b}" for e, b in mapping.items())
            raise ConfigRepoError(f"no code branch is mapped to {env!r} ({known}); "
                                  f"pass --code current or --code <branch>", 2)
        code = mapping[env]
    if code == "current":
        desc = {"source": "working tree", "root": str(code_root)}
        if is_git:
            _, head = git(["rev-parse", "--short", "HEAD"], cwd=code_root)
            _, br = git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=code_root)
            _, dirty = git(["status", "--porcelain", "--untracked-files=no"], cwd=code_root)
            desc.update(branch=br, commit=head, uncommitted_changes=bool(dirty.strip()))
        return CodeSource(code_root), desc
    if not is_git:
        raise ConfigRepoError(f"{code_root} is not a git repository; use --code current", 2)
    if not BRANCH_RE.match(code) or ".." in code:
        raise ConfigRepoError(f"invalid branch name {code!r}", 2)
    notes = []
    if fetch:
        rc, out = git(["fetch", "--quiet", "origin", f"+refs/heads/{code}:refs/remotes/origin/{code}"], cwd=code_root, timeout=180)
        if rc:
            notes.append(f"fetch of origin/{code} failed, using the last fetched state: {_git_error(out)}")
    chosen = None
    for cand in (f"origin/{code}", code):
        rc, sha = git(["rev-parse", "--verify", "--quiet", f"{cand}^{{commit}}"], cwd=code_root)
        if rc == 0 and sha:
            chosen = (cand, sha)
            break
    if not chosen:
        raise ConfigRepoError(f"branch {code!r} not found on origin or locally in {code_root}; "
                              "pass --code current or --code <branch>", 4)
    rc, local = git(["rev-parse", "--verify", "--quiet", f"{code}^{{commit}}"], cwd=code_root)
    if chosen[0].startswith("origin/") and rc == 0 and local and local != chosen[1]:
        notes.append(f"local branch {code} differs from origin/{code}; reviewing origin/{code}")
    _, when = git(["show", "-s", "--format=%cs %h %s", chosen[1]], cwd=code_root)
    desc = {"source": f"branch {chosen[0]}", "root": str(code_root), "commit": chosen[1][:10], "last_commit": when[:120],
            "notes": notes}
    return CodeSource(code_root, chosen[1]), desc


def branch_mapping():
    raw = setting("APP_CONFIG_CODE_BRANCHES", "staging:staging,uat:release")[0]
    out = {}
    for part in raw.split(","):
        env, _, br = part.partition(":")
        if env.strip() and br.strip():
            out[env.strip()] = br.strip()
    return out


def scan_code(src):
    """Collect what the code expects from its configuration."""
    paths = src.paths()
    is_test = lambda p: "/src/test/" in f"/{p}" or "/test/resources/" in f"/{p}"
    code_paths = [p for p in paths if p.endswith(CODE_SUFFIXES) and not is_test(p)]
    conf_paths = [p for p in paths if CONFIG_NAME.search(p) and "/resources/" in f"/{p}" and not is_test(p)]
    build_paths = [p for p in paths if p.split("/")[-1] in ("pom.xml", "settings.gradle", "settings.gradle.kts")]
    texts = src.read_many(code_paths + conf_paths + build_paths)
    refs = {}          # key -> dict(required: bool, defaults: set, where: [file:line], typed: set)
    prefixes = {}      # prefix -> [file]
    soft = set()       # keys bound through @ConfigurationProperties fields (normalized)

    def ref(key, default, where, required=None, typ=None):
        r = refs.setdefault(key, {"required": False, "defaults": [], "where": [], "types": set()})
        if required is None:
            required = default is None
        r["required"] |= required
        if default is not None and default not in r["defaults"]:
            r["defaults"].append(default)
        if len(r["where"]) < 5:
            r["where"].append(where)
        if typ:
            r["types"].add(typ)

    for p in code_paths:
        text = texts.get(p)
        if text is None:
            continue
        if "${" in text:
            for key, default, off in iter_placeholders(text):
                ref(key, default, f"{p}:{line_of(text, off)}")
        for m in GET_PROPERTY.finditer(text):
            extra = (m.group(3) or "").strip(", ")
            has_default = bool(extra) and not re.fullmatch(r"[\w.]+\.class", extra)
            if m.group(1) == "getRequiredProperty":
                required = True
            elif m.group(1) == "containsProperty":
                required = False
            else:
                required = False   # getProperty returns null when absent: the code decides
            ref(m.group(2), "<code default>" if has_default else None, f"{p}:{line_of(text, m.start())}", required=required)
        for m in CONFIG_PROPS.finditer(text):
            prefix = m.group(1).strip(".")
            prefixes.setdefault(prefix, []).append(p)
            for f in FIELD_JAVA.finditer(text[m.end():]):
                soft.add(norm(f"{prefix}.{kebab(f.group(1))}"))
        for m in COND_PROP.finditer(text):
            args = m.group(1)
            pre = re.search(r"prefix\s*=\s*\"([^\"]*)\"", args)
            names = re.findall(r"\"([^\"]+)\"", re.sub(r"(prefix|havingValue)\s*=\s*\"[^\"]*\"", "", args))
            for n in names:
                ref(f"{pre.group(1).strip('.')}.{n}" if pre else n, "<conditional>", f"{p}:{line_of(text, m.start())}", required=False)
        for m in VALUE_JAVA.finditer(text):
            single = re.fullmatch(r"\$\{([^}:]+)(?::[^}]*)?\}", m.group(1))
            if single:
                ref(single.group(1).strip(), None if ":" not in m.group(1) else "<default>", f"{p}:{line_of(text, m.start())}",
                    required=None if ":" in m.group(1) else True, typ=m.group(2).split("<")[0].lower())
        for m in VALUE_KOTLIN.finditer(text):
            single = re.fullmatch(r"\\?\$\{([^}:]+)(?::[^}]*)?\}", m.group(1))
            if single:
                ref(single.group(1).strip(), "<default>" if ":" in m.group(1) else None, f"{p}:{line_of(text, m.start())}",
                    typ=KOTLIN_TYPES.get(m.group(3), m.group(3).lower()))
    # the "<default>" markers above should not hide real defaults
    for r in refs.values():
        r["defaults"] = [d for d in r["defaults"] if d not in ("<default>",)] or (["<default>"] if not r["required"] and r["defaults"] else r["defaults"])

    bundled = {}       # file -> parsed entries {key: (value, line)}
    for p in conf_paths:
        text = texts.get(p)
        if text is None:
            continue
        parser = parse_yaml if p.endswith((".yml", ".yaml")) else parse_properties
        entries, _ = parser(text)
        bundled[p] = {k: (v, ln) for k, v, ln in entries}

    names = []
    for p in build_paths:
        text = texts.get(p, "")
        if p.endswith("pom.xml"):
            body = re.sub(r"<parent>.*?</parent>", "", re.sub(r"<!--.*?-->", "", text, flags=re.S), flags=re.S)
            body = re.sub(r"<(dependencies|dependencyManagement|build|profiles|plugins)>.*", "", body, flags=re.S)
            m = re.search(r"<artifactId>\s*([^<\s]+)\s*</artifactId>", body)
            if m:
                names.append((p, m.group(1)))
        else:
            m = re.search(r"rootProject\.name\s*=\s*['\"]([^'\"]+)['\"]", text)
            if m:
                names.append((p, m.group(1)))
    return {"refs": refs, "prefixes": prefixes, "soft": soft, "bundled": bundled, "build_names": names,
            "counts": {"code_files": len(code_paths), "config_files": len(conf_paths)}}


def active_bundled(bundled, env, env_values):
    """Bundled files Spring would load: application/bootstrap, plus profile files for active profiles."""
    profiles = {env}
    for key in ("spring.profiles.active", "spring.profiles.include"):
        v = env_values.get(key, ("", 0))[0]
        profiles |= {x.strip() for x in v.split(",") if x.strip() and "${" not in x}
    active, inactive = {}, []
    for path, entries in bundled.items():
        m = CONFIG_NAME.search(path)
        profile = (m.group(3) or "")[1:]
        if not profile or profile in profiles:
            active[path] = entries
        else:
            inactive.append(path)
    return active, inactive


def detect_service(scan, code_root, config_services, explicit=None):
    """-> (service, how). config_services: names that have the environment folder."""
    lower = {s.lower(): s for s in config_services}
    if explicit:
        if explicit.lower() in lower:
            return lower[explicit.lower()], "--service"
        raise ConfigRepoError(f"--service {explicit} has no folder for this environment", 4)
    cands = []
    for path, entries in scan["bundled"].items():
        v = entries.get("spring.application.name", ("", 0))[0].strip()
        if v and "${" not in v:
            cands.append((v, f"spring.application.name in {path}"))
    root_names = [n for n in scan["build_names"] if "/" not in n[0]]
    cands += [(n, f"artifactId/rootProject in {p}") for p, n in root_names]
    cands.append((Path(code_root).name, "repository folder name"))
    for name, how in cands:
        if name.lower() in lower:
            return lower[name.lower()], how
    module_hits = {lower[n.lower()] for _, n in scan["build_names"] if n.lower() in lower}
    if len(module_hits) == 1:
        return module_hits.pop(), "module artifactId"
    if len(module_hits) > 1:
        raise ConfigRepoError("several services of this repository have config: " + ", ".join(sorted(module_hits))
                              + "; pass --service <name>", 2)
    tried = ", ".join(sorted({c[0] for c in cands}))
    near = sorted({s for s in config_services for c, _ in cands if c and (c.lower() in s.lower() or s.lower() in c.lower())})[:8]
    raise ConfigRepoError(f"no config folder matches this repository (tried: {tried})"
                          + (f"; similar: {', '.join(near)}" if near else "") + "; pass --service <name>", 4)


def value_fits(typ, value):
    v = value.strip()
    if not v or "${" in v:
        return True
    t = typ.lower()
    if t in INT_TYPES:
        return re.fullmatch(r"[+-]?\d+", v.replace("_", "")) is not None
    if t in FLOAT_TYPES:
        return re.fullmatch(r"[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?", v) is not None
    if t in BOOL_TYPES:
        return v.lower() in ("true", "false", "yes", "no", "on", "off", "1", "0")
    if t in ("duration", "java.time.duration"):
        return re.fullmatch(r"[+-]?\d+(ns|us|ms|s|m|h|d)?|P[0-9TDHMS.]+", v, re.I) is not None
    return True


def library_prefixes():
    """Extra prefixes read by shared libraries/starters (APP_CONFIG_LIBRARY_PREFIXES, comma separated)."""
    raw = setting("APP_CONFIG_LIBRARY_PREFIXES")[0]
    return tuple(p.strip().lower().rstrip(".") + "." for p in raw.split(",") if p.strip())


def framework_key(key):
    k = key.lower()
    return any(k == p.rstrip(".") or k.startswith(p) for p in FRAMEWORK_PREFIXES + library_prefixes())


def review_against_code(files, scan, env):
    """Config of one environment compared with what the code expects."""
    out = []
    env_values = {}
    for name, f in files.items():
        for key, (value, line) in f["entries"].items():
            env_values.setdefault(key, (value, line, name))
    active, inactive = active_bundled(scan["bundled"], env, {k: (v[0], v[1]) for k, v in env_values.items()})
    bundled_values = {}
    for path, entries in active.items():
        for key, (value, line) in entries.items():
            bundled_values.setdefault(key, (value, line, path))
    env_norm = {norm(k): k for k in env_values}
    bundled_norm = {norm(k): k for k in bundled_values}
    refs = scan["refs"]

    # 1. what the code needs
    defaults_used, bundled_only = [], []
    for key, r in sorted(refs.items()):
        n = norm(key)
        where = r["where"][0] if r["where"] else ""
        if n in env_norm:
            ek = env_norm[n]
            value, line, fname = env_values[ek]
            for typ in r["types"]:
                if not value_fits(typ, value):
                    out.append(finding("error", "type-mismatch", fname, ek,
                                       f"{where} expects {typ}, value is {shown(ek, value)!s}", line))
            continue
        if n in bundled_norm:
            bundled_only.append(key)
            continue
        if any(n == p or n.startswith(p + ".") for p in (norm(x) for x in scan["prefixes"])):
            continue
        if r["required"]:
            if ENV_VAR_KEY.match(key):
                out.append(finding("warn", "env-var-required", "", key,
                                   f"{where} needs environment variable {key} (not in the config files; check the deployment)"))
            else:
                out.append(finding("error", "missing-required", "", key,
                                   f"{where} needs it without a default; neither the {env} config nor the code repository sets it "
                                   "(start-up fails: could not resolve placeholder)"))
        elif r["defaults"]:
            d = r["defaults"][0]
            defaults_used.append(f"{key}={'<secret>' if is_secret_key(key) else mask(d) if d else '(empty)'}")
    if defaults_used:
        out.append(finding("info", "code-default-used", "", "", f"{len(defaults_used)} key(s) not set for {env}, code default used: "
                           + ", ".join(defaults_used[:10]) + (f" (+{len(defaults_used) - 10} more)" if len(defaults_used) > 10 else ""),
                           keys=defaults_used))
    if bundled_only:
        out.append(finding("info", "code-repo-value-used", "", "", f"{len(bundled_only)} key(s) come only from the code repository's "
                           "application files: " + ", ".join(bundled_only[:10]) + (" ..." if len(bundled_only) > 10 else ""),
                           keys=bundled_only))

    # 2. what the config sets but nothing reads
    if scan["counts"]["code_files"] < 5:
        out.append(finding("info", "unused-not-checked", "", "", f"only {scan['counts']['code_files']} source file(s) in the code: "
                           "keys are probably read by libraries, unused keys not checked"))
        env_values_for_unused = {}
    else:
        env_values_for_unused = env_values
    referenced = {norm(k) for k in refs}
    for src in list(env_values.values()) + list(bundled_values.values()):
        if "${" in src[0]:
            referenced |= {norm(k) for k, _, _ in iter_placeholders(src[0])}
    prefixes = [norm(p) for p in scan["prefixes"]]
    unused = {}
    for key, (value, line, fname) in env_values_for_unused.items():
        n = norm(key)
        if n in referenced or n in scan["soft"] or framework_key(key):
            continue
        if any(n == p or n.startswith(p) for p in prefixes):
            continue
        unused.setdefault(fname, []).append(key)
    code_keys = sorted(refs)
    for fname, keys in unused.items():
        hints = []
        for k in keys:
            close = difflib.get_close_matches(k, code_keys, n=1, cutoff=0.85)
            if close and norm(close[0]) not in env_norm:
                hints.append(f"{k} -> {close[0]}?")
        msg = (f"{len(keys)} key(s) not read by this code, Spring or another value (obsolete, renamed, or read by a "
               "shared library: list its prefixes in APP_CONFIG_LIBRARY_PREFIXES): "
               + ", ".join(sorted(keys)[:12]) + (f" (+{len(keys) - 12} more)" if len(keys) > 12 else ""))
        out.append(finding("warn" if hints else "info", "possibly-unused", fname, "", msg, keys=sorted(keys), renamed=hints))
        for h in hints:
            out.append(finding("warn", "possibly-renamed", fname, h.split(" ->")[0], f"code reads {h.split('-> ')[1].rstrip('?')}, "
                               "the config sets a similar key", env_values[h.split(" ->")[0]][1]))

    # 3. placeholders inside config values that nothing defines
    defined = set(env_norm) | set(bundled_norm)
    for key, (value, line, fname) in env_values.items():
        if "${" not in value:
            continue
        for ref_key, default, _ in iter_placeholders(value):
            if default is not None or norm(ref_key) in defined:
                continue
            if ENV_VAR_KEY.match(ref_key):
                out.append(finding("warn", "env-var-required", fname, key, f"value needs environment variable {ref_key}", line))
            else:
                out.append(finding("error", "unresolved-placeholder", fname, key, f"value refers to ${{{ref_key}}}, defined nowhere", line))

    # 4. code repository files
    for path, entries in scan["bundled"].items():
        for key, (value, line) in entries.items():
            if is_plain_secret(key, value) or CRED_IN_URL.search(value):
                out.append(finding("error", "secret-in-code-repo", path, key, "secret committed in the code repository", line))
    overrides = []
    for key, (value, line, fname) in env_values.items():
        b = bundled_values.get(bundled_norm.get(norm(key), ""), None)
        if b and b[0].strip() != value.strip() and CONFIG_NAME.search(b[2]).group(3):
            overrides.append(f"{key} ({b[2]})")
    if overrides:
        out.append(finding("info", "profile-file-also-sets", "", "", "the code repository's profile file also sets: "
                           + ", ".join(overrides[:10]) + "; which one wins depends on how the config is loaded"))
    return out, {"active_code_config": sorted(active), "inactive_code_config": sorted(inactive)}


# ---------------------------------------------------------------- command

ORDER = {"error": 0, "warn": 1, "info": 2}


def run(args):
    env = args.env
    check_env(env)
    compare = [e.strip() for e in (args.compare or "").split(",") if e.strip()]
    for e in compare:
        check_env(e)
        if e == env:
            raise ConfigRepoError(f"--compare {e} is the reviewed environment", 2)
    fetch = not args.no_fetch
    opened = {}
    config_path, config_commit = open_repo(repo_var_for(env), fetch)
    opened[repo_var_for(env)] = (config_path, config_commit)
    notes, code_desc, scan, service, how = [], None, None, args.service, "--service"
    if not args.no_code:
        src, code_desc = resolve_code(args.repo, env, args.code, fetch)
        notes += code_desc.pop("notes", [])
        scan = scan_code(src)
        service, how = detect_service(scan, code_desc["root"], services_with(config_path, env), args.service)
    elif not service:
        raise ConfigRepoError("--no-code needs --service <name>", 2)
    data = load_service_env(service, env, fetch, opened)
    files = data["files"]
    others = {}
    for e in compare:
        try:
            others[e] = load_service_env(service, e, fetch, opened)["files"]
        except ConfigRepoError as exc:
            notes.append(f"{e} not compared: {exc}")
    findings = review_config(files, others, env)
    extra = {}
    if scan is not None:
        code_findings, extra = review_against_code(files, scan, env)
        findings += code_findings
    for e, other_files in others.items():
        findings += drift(files, e, other_files, env)
    findings.sort(key=lambda f: (ORDER[f["level"]], f["rule"], f["file"], f["key"]))
    counts = {lvl: sum(1 for f in findings if f["level"] == lvl) for lvl in ORDER}
    return {
        "service": service, "service_detected_from": how, "environment": env,
        "config": {"repository": data["repo"], "commit": data["commit"], "files": sorted(files)},
        "code": dict(code_desc or {}, **(scan["counts"] if scan else {}), **extra) if scan else None,
        "compared": sorted(others), "notes": notes, "counts": counts, "findings": findings,
    }


def print_text(r):
    print(f"{r['service']} / {r['environment']}   (service from {r['service_detected_from']})")
    c = r["config"]
    print(f"config: {c['repository']} commit {c['commit']}: {', '.join(c['files']) or '(no files)'}")
    if r["code"]:
        k = r["code"]
        line = f"code:   {k['source']}" + (f" ({k['branch']})" if k.get("branch") else "")
        if k.get("commit"):
            line += f" @ {k['commit']}"
        if k.get("last_commit"):
            line += f" ({k['last_commit']})"
        if k.get("uncommitted_changes"):
            line += " + uncommitted changes"
        print(line + f"; {k['code_files']} source file(s), code config: {', '.join(k['active_code_config']) or '(none)'}")
    else:
        print("code:   not read (--no-code)")
    print(f"compared with: {', '.join(r['compared']) or '(nothing)'}")
    for n in r["notes"]:
        print(f"note: {n}")
    for f in r["findings"]:
        where = (f["file"] or "") + (f":{f['line']}" if f.get("line") else "")
        subject = " ".join(x for x in (where, f["key"]) if x)
        print(f"{f['level']:5} {f['rule']:22} " + (f"{subject}: " if subject else "") + f["message"])
    n = r["counts"]
    print(f"{n['error']} error(s), {n['warn']} warning(s), {n['info']} info")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--env", required=True, help="environment to review: staging, uat, production, ...")
    ap.add_argument("--code", help="current (working tree) or a branch; default from APP_CONFIG_CODE_BRANCHES")
    ap.add_argument("--repo", default=".", help="code repository, default the current folder")
    ap.add_argument("--service", help="config folder name, when it cannot be detected")
    ap.add_argument("--compare", default="", help="other environments to compare with, e.g. uat,production")
    ap.add_argument("--no-code", action="store_true", help="configuration rules only (needs --service)")
    ap.add_argument("--no-fetch", action="store_true", help="use the cached config clone and the last fetched branch")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    try:
        report = run(args)
    except ConfigRepoError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return exc.code
    except subprocess.TimeoutExpired:
        print("error: git timed out", file=sys.stderr)
        return 5
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print_text(report)
    return 1 if report["counts"]["error"] else 0


if __name__ == "__main__":
    sys.exit(main())
