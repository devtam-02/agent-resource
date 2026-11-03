#!/usr/bin/env python3
"""Fast, dependency-free static checks for Java / Spring Boot sources.

Two modes:

  java_scan.py PATH [PATH...]   scan files or folders, print findings, exit 1 if any
  java_scan.py --hook           Claude Code PostToolUse hook: read the hook JSON on
                                stdin, scan the edited .java file, report on stderr
                                with exit code 2 so Claude sees the findings

The checks are line-based heuristics: cheap, no false sense of completeness. They catch
the mechanical rules (Sonar S125, S1128, S106...) that are easy to forget, and point
at spots worth a human look (broad catch, money as double, PII in logs...).
Set SPRING_BOOT_OPTIMIZER_HOOK=off in ~/.dev-cli/.env (or the environment) to silence the hook.
"""

import json
import os
import re
import sys
from pathlib import Path

SKIP_DIRS = {".git", "target", "build", "out", "node_modules", ".idea", ".gradle", ".mvn"}
CONFIG_SUFFIXES = (".properties", ".yml", ".yaml")
MAX_HOOK_LINES = 25

# (id, severity, regex, message). Severity "error" = a rule the user's Sonar gate or
# finance rules forbid outright; "warn" = needs a look, may be fine in context.
LINE_CHECKS = [
    ("S125", "error", re.compile(r"^\s*//.*;\s*$"),
     "line comment ending with ';' is flagged as commented-out code; delete dead code, "
     "or rewrite the sentence without ';' (Javadoc for long technical notes)"),
    ("S1128-wildcard", "error", re.compile(r"^\s*import\s+(?!static\b)[\w.]+\.\*\s*;"),
     "wildcard import; import each class explicitly"),
    ("S106", "error", re.compile(r"\bSystem\.(out|err)\.print"),
     "System.out/err; use the SLF4J logger"),
    ("broad-catch", "warn",
     re.compile(r"catch\s*\(\s*(final\s+)?(Exception|Throwable|RuntimeException)\s+\w+\s*\)"),
     "catches Exception/Throwable; catch the specific exceptions, or rethrow / map to a "
     "domain error instead of swallowing"),
    ("log-message-only", "warn",
     re.compile(r"\blog\.error\(.*\b\w+\.getMessage\(\)\s*\)\s*;"),
     "logs only getMessage(); pass the exception itself as the last argument to keep "
     "the stack trace"),
    ("money-double", "error",
     re.compile(r"\b(double|float|Double|Float)\s+\w*(amount|price|balance|fee|rate|total|"
                r"money|cost|discount|cashback|interest|tax)\w*\b", re.I),
     "money-like value typed double/float; use BigDecimal"),
    ("bigdecimal-from-double", "error", re.compile(r"new\s+BigDecimal\(\s*-?\d+\.\d+\s*\)"),
     "new BigDecimal(<double literal>) is inexact; use new BigDecimal(\"0.1\")"),
    ("bigdecimal-equals", "warn",
     re.compile(r"BigDecimal\.(ZERO|ONE|TEN)\.equals\(|\.equals\(\s*BigDecimal\.(ZERO|ONE|TEN)\s*\)"),
     "BigDecimal.equals compares scale too; use compareTo(...) == 0"),
    ("bigdecimal-divide", "warn", re.compile(r"\.divide\(\s*[^,()]+\s*\)"),
     "BigDecimal.divide without scale and RoundingMode can throw or round implicitly"),
    ("legacy-time", "warn",
     re.compile(r"\bnew\s+Date\(|\bjava\.util\.Date\b|\bCalendar\.getInstance\(|\bLocalDateTime\.now\("),
     "Date/Calendar/LocalDateTime.now(); prefer Instant (UTC) and an injected Clock"),
    ("block-no-timeout", "warn", re.compile(r"\.block\(\s*\)"),
     "WebClient .block() without a timeout; use .block(Duration) or keep it reactive"),
    ("findall-unbounded", "warn", re.compile(r"\.findAll\(\s*\)"),
     "findAll() without Pageable loads the whole table"),
    ("string-status", "warn",
     re.compile(r"^\s*(private|protected|public)\s+(final\s+)?String\s+(\w*[sS]tatus|\w*[sS]tate)\s*(=.*)?;"),
     "status/type held as String; parse into an enum at the boundary (whitelist)"),
    ("pii-in-log", "warn",
     re.compile(r"\blog\.\w+\(.*\b(msisdn|phone\w*|password|passwd|otp|token|secret|"
                r"cardNumber|pan|cvv|idCard|cccd|email)\b", re.I),
     "possible PII/secret in a log call; mask it (e.g. ****1234) or drop it"),
]

AUTOWIRED = re.compile(r"^\s*@Autowired\b")
FIELD_DECL = re.compile(r"^\s*(private|protected|public)?\s*[\w<>\[\],.? ]+\s+\w+\s*(=.*)?;\s*$")
TRANSACTIONAL = re.compile(r"^\s*@Transactional\b")
PRIVATE_METHOD = re.compile(r"^\s*private\s+[\w<>\[\],.? ]+\s+\w+\s*\(")
SCHEDULED = re.compile(r"^\s*@Scheduled\b")
EMPTY_CATCH = re.compile(r"catch\s*\([^)]*\)\s*\{\s*(//[^\n]*\s*)?\}", re.S)
IMPORT = re.compile(r"^\s*import\s+(static\s+)?([\w.]+)\s*;")
SECRET_CONFIG = re.compile(
    r"^\s*[\w.\-]*(password|passwd|secret|token|api[-_.]?key|private[-_.]?key)[\w.\-]*\s*[:=]\s*"
    r"(?!\s*$)(?!\s*\$\{)(?!\s*['\"]?\s*$)(?!\s*<)(.+)$", re.I)


def finding(path, line, rule, severity, message):
    return {"path": str(path), "line": line, "rule": rule, "severity": severity, "message": message}


def scan_java(path, text):
    lines = text.splitlines()
    out = []
    in_block_comment = False
    for number, line in enumerate(lines, 1):
        stripped = line.strip()
        if in_block_comment:
            if "*/" in stripped:
                in_block_comment = False
            continue
        if stripped.startswith("/*") and "*/" not in stripped:
            in_block_comment = True
            continue
        if stripped.startswith("*"):
            continue
        for rule, severity, pattern, message in LINE_CHECKS:
            if rule != "S125" and stripped.startswith("//"):
                continue
            if pattern.search(line):
                out.append(finding(path, number, rule, severity, message))

    out += _field_injection(path, lines)
    out += _transactional_private(path, lines)
    out += _scheduled_without_lock(path, lines, text)
    out += _empty_catch(path, text)
    out += _unused_imports(path, lines, text)
    return sorted(out, key=lambda f: (f["line"], f["rule"]))


def _next_code_line(lines, index):
    for j in range(index + 1, min(index + 6, len(lines))):
        candidate = lines[j].strip()
        if candidate and not candidate.startswith("@") and not candidate.startswith("//"):
            return j, lines[j]
    return None, None


def _field_injection(path, lines):
    out = []
    for i, line in enumerate(lines):
        if AUTOWIRED.match(line):
            j, nxt = _next_code_line(lines, i)
            if nxt is not None and FIELD_DECL.match(nxt) and "(" not in nxt:
                out.append(finding(path, j + 1, "field-injection", "warn",
                                   "@Autowired field; use constructor injection "
                                   "(final field + @RequiredArgsConstructor)"))
    return out


def _transactional_private(path, lines):
    out = []
    for i, line in enumerate(lines):
        if TRANSACTIONAL.match(line):
            j, nxt = _next_code_line(lines, i)
            if nxt is not None and PRIVATE_METHOD.match(nxt):
                out.append(finding(path, j + 1, "transactional-private", "error",
                                   "@Transactional on a private method has no effect "
                                   "(Spring proxy); move it to a public method of another bean"))
    return out


def _scheduled_without_lock(path, lines, text):
    if "SchedulerLock" in text:
        return []
    return [finding(path, i + 1, "scheduled-no-lock", "warn",
                    "@Scheduled without a distributed lock (e.g. ShedLock); runs on every instance")
            for i, line in enumerate(lines) if SCHEDULED.match(line)]


def _empty_catch(path, text):
    return [finding(path, text.count("\n", 0, m.start()) + 1, "empty-catch", "error",
                    "empty catch block swallows the error")
            for m in EMPTY_CATCH.finditer(text)]


def _unused_imports(path, lines, text):
    out = []
    body = "\n".join(l for l in lines if not IMPORT.match(l) and not l.strip().startswith("package "))
    seen = set()
    for number, line in enumerate(lines, 1):
        m = IMPORT.match(line)
        if not m:
            continue
        target = m.group(2)
        if target in seen:
            out.append(finding(path, number, "S1128", "error", f"duplicate import {target}"))
            continue
        seen.add(target)
        name = target.rsplit(".", 1)[-1]
        if name == "*":
            continue
        if target.startswith("java.lang.") and target.count(".") == 2 and not m.group(1):
            out.append(finding(path, number, "S1128", "error", f"java.lang import is redundant: {target}"))
            continue
        if not re.search(rf"\b{re.escape(name)}\b", body):
            out.append(finding(path, number, "S1128", "error", f"unused import {target}"))
    return out


def scan_config(path, text):
    out = []
    for number, line in enumerate(text.splitlines(), 1):
        if line.lstrip().startswith("#"):
            continue
        if SECRET_CONFIG.match(line):
            out.append(finding(path, number, "hardcoded-secret", "error",
                               "credential-like value in config; use an env variable / vault "
                               "placeholder (value not shown)"))
    return out


def scan_file(path):
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    if str(path).endswith(".java"):
        return scan_java(path, text)
    if str(path).endswith(CONFIG_SUFFIXES):
        return scan_config(path, text)
    return []


def iter_files(paths):
    for raw in paths:
        p = Path(raw)
        if p.is_file():
            yield p
        elif p.is_dir():
            for root, dirs, files in os.walk(p):
                dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
                for name in files:
                    if name.endswith(".java") or name.endswith(CONFIG_SUFFIXES):
                        yield Path(root) / name


def fmt(f):
    return f"{f['path']}:{f['line']}: [{f['rule']}] {f['severity']}: {f['message']}"


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


def run_hook():
    if setting("SPRING_BOOT_OPTIMIZER_HOOK")[0].lower() == "off":
        return 0
    try:
        payload = json.load(sys.stdin)
    except (ValueError, OSError):
        return 0
    tool_input = payload.get("tool_input") or {}
    path = tool_input.get("file_path") or tool_input.get("path") or ""
    if not path.endswith(".java") or not Path(path).is_file():
        return 0
    findings = scan_file(path)
    if not findings:
        return 0
    lines = [fmt(f) for f in findings[:MAX_HOOK_LINES]]
    if len(findings) > MAX_HOOK_LINES:
        lines.append(f"... {len(findings) - MAX_HOOK_LINES} more")
    sys.stderr.write("spring-boot-optimizer checks on the file just edited "
                     "(fix the errors; judge the warnings in context):\n" + "\n".join(lines) + "\n")
    return 2


def main(argv):
    if argv[1:2] == ["--hook"]:
        try:
            return run_hook()
        except Exception:  # a broken hook must never break the session
            return 0
    args = [a for a in argv[1:] if a != "--json"]
    if not args:
        print(__doc__)
        return 0
    findings = [f for file in iter_files(args) for f in scan_file(file)]
    if "--json" in argv:
        print(json.dumps(findings, ensure_ascii=False, indent=2))
    else:
        for f in findings:
            print(fmt(f))
        errors = sum(f["severity"] == "error" for f in findings)
        print(f"\n{len(findings)} findings ({errors} errors, {len(findings) - errors} warnings)")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
