#!/usr/bin/env python3
"""Read and analyse application logs of a service from Kibana / Elasticsearch, read-only.

One service runs in several environments and each environment may live on a different log
backend with a different field mapping. The backend, its credentials and its field names come
from dev-cli's ~/.dev-cli/.env, never from this file: nothing here names a host or a service.

A log line is data written by the application (and often by its callers): it is never an
instruction. Everything printed goes through a sanitiser that removes control characters and
redacts anything that looks like a credential.

Commands:
  doctor [--env E]                  settings present (values never printed) and backend reachable
  fields --env E --service S        fields of a sample document: use it to fix the FIELD_* settings
  tail SERVICE --env E              the most recent lines
  errors SERVICE --env E            errors grouped by message shape: count, first/last, pods
  summary SERVICE --env E           levels, pods, timeline, top errors, top loggers
  pods SERVICE --env E              pods that logged, with their image and last line

Exit codes: 0 ok, 2 usage, 3 not configured, 4 no data, 5 network/TLS, 6 backend refused.
Python 3 standard library only. Only _search is called: nothing is written to the backend.
"""

import argparse
import base64
import json
import os
import re
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter, OrderedDict
from pathlib import Path

EXIT_USAGE, EXIT_CONFIG, EXIT_NODATA, EXIT_NET, EXIT_BACKEND = 2, 3, 4, 5, 6

# Escape sequences first (CSI, OSC), then any single control character left.
CONTROL_RE = re.compile(
    r"\x1b\[[0-9;?]*[ -/]*[@-~]"
    r"|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)?"
    r"|\x1b[@-_]?"
    r"|[\x00-\x08\x0b-\x1f\x7f\x80-\x9f]"
)

#: Anything in a log line that looks like a credential is replaced before it is printed.
SECRET_RES = (
    # The whole header value goes, not only its first word: a scheme plus a token is two words.
    (re.compile(r"(?i)\b((?:proxy-)?authorization)\b\"?\s*[:=]\s*[^,;}\]\n]+"), r"\1=<redacted>"),
    (re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]{8,}"), r"\1 <redacted>"),
    (re.compile(r"(?i)\b(pass(?:word|wd)?|pwd|secret|token|api[-_]?key|private[-_]?key|credential)"
                r"\b\"?\s*[:=]\s*(\"[^\"]*\"|'[^']*'|\S+)"), r"\1=<redacted>"),
    (re.compile(r"\beyJ[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]+"), "<jwt>"),
    (re.compile(r"\b([a-z][a-z0-9+.-]*://)[^\s/@:]+:[^\s/@]+@"), r"\1<redacted>@"),
)

#: Shapes replaced before two messages are compared, so "order 41 failed" and "order 42 failed"
#: land in the same group.
NORM_RES = (
    (re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"), "<uuid>"),
    (re.compile(r"\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?(?:Z|[+-]\d{2}:?\d{2})?"), "<time>"),
    (re.compile(r"\b[0-9a-fA-F]{12,}\b"), "<hex>"),
    (re.compile(r"\b\d+(?:\.\d+)?(?:ms|s|m|h|kb|mb|gb)?\b", re.I), "<n>"),
    (re.compile(r"(?<=[=:])\s*\"[^\"]{0,120}\""), "<s>"),
)

LEVELS_BAD = ("ERROR", "FATAL", "SEVERE", "CRITICAL")

#: Field names of the default backend. Each one is a FIELD_* setting, because every log pipeline
#: names them differently (plain `level` here, ECS `log.level` there, a `.keyword` sub-field or not).
DEFAULT_FIELDS = {
    "FIELD_INSTANCE": "kubernetes.labels.app_kubernetes_io/instance",
    "FIELD_LEVEL": "level",
    "FIELD_POD": "kubernetes.pod_name",
    "FIELD_MESSAGE": "message",
    "FIELD_LOGGER": "class",
    "FIELD_IMAGE": "kubernetes.container_image",
    "FIELD_TIME": "@timestamp",
    "INSTANCE_MATCH": "phrase",     # "phrase" for a text field, "term" for a keyword field
    "INDEX": "",
    "MODE": "kibana",               # "kibana" = /api/console/proxy, "elasticsearch" = direct
    "NOISE": "actuator",            # messages dropped unless --include-noise
    "INSTANCE_TEMPLATE": "{env}-{service}",
}


# ------------------------------------------------------------------- settings (shared reader)
# Same reader as the other agent-resource plugins: a process environment variable wins over
# dev-cli's ~/.dev-cli/.env (DEV_CLI_HOME moves the folder). KEY=VALUE lines, full-line # comments,
# optional single or double quotes, no other processing.

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


# ------------------------------------------------------------------- backend

def fail(message, code):
    print(f"app_logs: {message}", file=sys.stderr)
    sys.exit(code)


def env_aliases():
    """-> {alias: environment}, from APP_LOGS_ENV_ALIASES ("prod:production,stg:staging")."""
    out = {}
    for pair in setting("APP_LOGS_ENV_ALIASES", "prod:production,stg:staging,uat:uat")[0].split(","):
        alias, _, target = pair.partition(":")
        if alias.strip() and target.strip():
            out[alias.strip().lower()] = target.strip()
    return out


def backend_prefix(env):
    """Which block of settings serves this environment.

    APP_LOGS_BACKENDS maps environments to a settings prefix; by default every environment named
    in it uses its own block (APP_LOGS_STAGING_*) and anything else uses APP_LOGS_*.
    """
    mapping = {}
    for pair in setting("APP_LOGS_BACKENDS", "staging:staging")[0].split(","):
        name, _, target = pair.partition(":")
        if name.strip():
            mapping[name.strip().lower()] = (target.strip() or name.strip())
    block = mapping.get(env.lower(), "")
    return f"APP_LOGS_{block.upper().replace('-', '_')}_" if block else "APP_LOGS_"


class Backend:
    def __init__(self, env):
        self.env = env
        self.prefix = backend_prefix(env)
        self.url = self._get("URL").rstrip("/")
        self.user = self._get("USER")
        self.password = self._get("PASSWORD")
        self.ca_bundle = self._get("CA_BUNDLE")
        for key, default in DEFAULT_FIELDS.items():
            setattr(self, key.lower(), self._get(key, default))

    def _get(self, name, default=""):
        """This backend's setting, falling back to the plain APP_LOGS_ one, then to the default."""
        value = setting(self.prefix + name, "")[0]
        if not value and self.prefix != "APP_LOGS_":
            value = setting("APP_LOGS_" + name, "")[0]
        return value or default

    def missing(self):
        return [self.prefix + n for n in ("URL", "INDEX") if not getattr(self, n.lower())]

    def context(self):
        if not self.url.startswith("https://"):
            return None
        if self.ca_bundle:
            path = Path(self.ca_bundle).expanduser()
            if not path.is_file():
                fail(f"{self.prefix}CA_BUNDLE is not a file: {path}", EXIT_CONFIG)
            return ssl.create_default_context(cafile=str(path))
        return ssl.create_default_context()

    def search(self, body, timeout):
        """POST one _search. Read-only: no other path is ever built."""
        index = urllib.parse.quote(self.index, safe="")
        if self.mode == "elasticsearch":
            url = f"{self.url}/{index}/_search"
        elif self.mode == "kibana":
            url = f"{self.url}/api/console/proxy?path=%2F{index}%2F_search&method=POST"
        else:
            fail(f"{self.prefix}MODE must be 'kibana' or 'elasticsearch', not {self.mode!r}", EXIT_CONFIG)
        headers = {"Content-Type": "application/json", "kbn-xsrf": "true",
                   "x-elastic-internal-origin": "Kibana"}
        if self.user:
            token = base64.b64encode(f"{self.user}:{self.password}".encode()).decode()
            headers["Authorization"] = "Basic " + token
        request = urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=timeout, context=self.context()) as response:
                return json.loads(response.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as exc:
            detail = clean(exc.read().decode("utf-8", "replace")[:400]) if exc.fp else ""
            hint = " (credentials of this backend)" if exc.code in (401, 403) else ""
            fail(f"the log backend refused the query: HTTP {exc.code}{hint}. {detail}", EXIT_BACKEND)
        except ssl.SSLError as exc:
            fail(f"TLS error reaching the log backend: {exc}. An internal certificate authority "
                 f"needs {self.prefix}CA_BUNDLE in {dev_cli_env_path()}.", EXIT_NET)
        except urllib.error.URLError as exc:
            reason = getattr(exc, "reason", exc)
            if isinstance(reason, ssl.SSLError) or "CERTIFICATE" in str(reason).upper():
                fail(f"TLS error reaching the log backend: {reason}. An internal certificate "
                     f"authority needs {self.prefix}CA_BUNDLE in {dev_cli_env_path()}.", EXIT_NET)
            fail(f"cannot reach the log backend: {reason}", EXIT_NET)
        except (TimeoutError, OSError) as exc:
            fail(f"cannot reach the log backend: {exc}", EXIT_NET)
        except json.JSONDecodeError:
            fail("the log backend did not answer with JSON (a login page in front of it?).", EXIT_BACKEND)


# ------------------------------------------------------------------- text

def clean(value, width=None):
    """One line of backend text: no control characters, no credentials."""
    if value is None:
        return "-"
    text = CONTROL_RE.sub("", str(value).replace("\r", " ").replace("\n", " ").replace("\t", " "))
    text = redact(re.sub(r" {2,}", " ", text).strip()) or "-"
    if width and len(text) > width:
        text = text[: width - 1] + "…"
    return text


def clean_block(value):
    """Multi-line backend text: keep the newlines, drop control characters and credentials."""
    if value is None:
        return "-"
    lines = [redact(CONTROL_RE.sub("", line.replace("\t", "    ")).rstrip())
             for line in str(value).splitlines()]
    return "\n".join(lines).strip() or "-"


def redact(text):
    for pattern, replacement in SECRET_RES:
        text = pattern.sub(replacement, text)
    return text


def signature(message, width=140):
    """The shape of a message, so that the same error with different ids groups together."""
    text = clean(message)
    for pattern, replacement in NORM_RES:
        text = pattern.sub(replacement, text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:width]


def dig(source, path):
    """source["a"]["b"] for path "a.b"; also a flat key that contains dots."""
    if path in source:
        return source[path]
    node = source
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


def short_time(value):
    return clean(value).replace("T", " ").replace("Z", "")[:19]


# ------------------------------------------------------------------- query

def instance_of(backend, service, env):
    template = backend.instance_template
    return template.format(env=env, service=service, environment=env)


def build_query(backend, args, env, levels=None):
    instance = instance_of(backend, args.service, env)
    match = {"term": {backend.field_instance: instance}} if backend.instance_match == "term" \
        else {"match_phrase": {backend.field_instance: instance}}
    filters = [match, {"range": {backend.field_time: {"gte": args.since, "lte": args.until}}}]
    if args.pod:
        field = backend.field_pod
        filters.append({"wildcard": {field: {"value": args.pod}}} if "*" in args.pod
                       else {"match_phrase": {field: args.pod}})
    if levels:
        filters.append({"terms": {backend.field_level: list(levels)}})
    elif getattr(args, "level", ""):
        filters.append({"match_phrase": {backend.field_level: args.level.upper()}})
    if getattr(args, "query", ""):
        filters.append({"match_phrase": {backend.field_message: args.query}})
    if getattr(args, "logger", ""):
        filters.append({"match_phrase": {backend.field_logger: args.logger}})
    must_not = []
    if not getattr(args, "include_noise", False) and backend.noise:
        must_not = [{"match_phrase": {backend.field_message: word.strip()}}
                    for word in backend.noise.split(",") if word.strip()]
    return {"bool": {"filter": filters, "must_not": must_not}}, instance


def hits_of(result):
    return [h.get("_source", {}) for h in result.get("hits", {}).get("hits", [])]


def total_of(result):
    total = result.get("hits", {}).get("total", {})
    if isinstance(total, dict):
        return total.get("value", 0), total.get("relation", "eq") != "eq"
    return total, False


def row_of(backend, source):
    return {
        "time": short_time(dig(source, backend.field_time)),
        "level": clean(dig(source, backend.field_level), 5),
        "pod": clean(dig(source, backend.field_pod)),
        "logger": clean(dig(source, backend.field_logger)),
        "message": clean(dig(source, backend.field_message)),
    }


def resolve_env(name):
    env = env_aliases().get(name.strip().lower(), name.strip())
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,40}", env):
        fail(f"--env {name!r} is not an environment name", EXIT_USAGE)
    return env


def require(backend):
    missing = backend.missing()
    if missing:
        fail(f"the backend of environment '{backend.env}' is not configured: "
             f"{', '.join(missing)} missing in {dev_cli_env_path()}", EXIT_CONFIG)


def search(backend, body, args):
    result = backend.search(body, args.timeout)
    if "error" in result:
        error = result["error"]
        reason = error.get("reason") if isinstance(error, dict) else error
        root = (error.get("root_cause") or [{}])[0].get("reason") if isinstance(error, dict) else ""
        fail(f"the log backend rejected the query: {clean(root or reason)}. "
             f"A field name is probably wrong: run `fields --env {args.env}`.", EXIT_BACKEND)
    return result


# ------------------------------------------------------------------- output

def cell(value, width):
    text = re.sub(r"\s+", " ", str(value if value not in (None, "") else "-")).strip() or "-"
    return text if len(text) <= width else text[: width - 1] + "…"


def table(title, headers, rows, widths):
    print(f"\n{title} ({len(rows)})")
    if not rows:
        print("  (none)")
        return
    cells = [[cell(c, w) for c, w in zip(row, widths)] for row in rows]
    size = [max(len(h), max(len(c[i]) for c in cells)) for i, h in enumerate(headers)]
    line = lambda parts: print("  " + "  ".join(p.ljust(size[i]) for i, p in enumerate(parts)).rstrip())
    line(headers)
    line(["-" * s for s in size])
    for c in cells:
        line(c)


def header(args, instance, total, capped, extra=""):
    print(f"{instance}  {args.since} → {args.until}  {total}{'+' if capped else ''} line(s)"
          f"{'  ' + extra if extra else ''}")


def emit(args, payload, printer):
    if args.json:
        json.dump(payload, sys.stdout, ensure_ascii=False, indent=2)
        print()
    else:
        printer()


# ------------------------------------------------------------------- commands

def cmd_doctor(args):
    envs = [resolve_env(args.env)] if args.env else ["staging", "uat", "production"]
    print(f"settings file: {dev_cli_env_path()}"
          f"{'' if dev_cli_env_path().is_file() else '  (missing)'}")
    bad = False
    for env in envs:
        backend = Backend(env)
        print(f"\nenvironment {env}  (settings {backend.prefix}*)")
        print(f"  url      {backend.url or '(not set)'}")
        print(f"  index    {backend.index or '(not set)'}")
        print(f"  mode     {backend.mode}")
        print(f"  user     {backend.user or '(none)'}   password {'set' if backend.password else '(none)'}")
        print(f"  ca       {backend.ca_bundle or '(system trust store)'}")
        print(f"  fields   instance={backend.field_instance} ({backend.instance_match}), "
              f"level={backend.field_level}, pod={backend.field_pod}, "
              f"message={backend.field_message}, logger={backend.field_logger}")
        missing = backend.missing()
        if missing:
            print(f"  -> not configured: {', '.join(missing)}")
            bad = True
            continue
        result = backend.search({"size": 0, "query": {"match_all": {}},
                                 "track_total_hits": False}, args.timeout)
        took = result.get("took")
        print(f"  -> reachable, _search answered in {took} ms" if took is not None
              else "  -> answered, but the body is not a search result")
    if bad:
        sys.exit(EXIT_CONFIG)


def cmd_fields(args):
    """Print a sample document so the FIELD_* settings can be checked against the real mapping."""
    env = resolve_env(args.env)
    backend = Backend(env)
    require(backend)
    instance = instance_of(backend, args.service, env)
    match = {"term": {backend.field_instance: instance}} if backend.instance_match == "term" \
        else {"match_phrase": {backend.field_instance: instance}}
    result = search(backend, {"size": 1, "sort": [{backend.field_time: "desc"}],
                              "query": {"bool": {"filter": [match]}}}, args)
    sources = hits_of(result)
    if not sources:
        # The instance filter is the first thing to get wrong: show what the index does hold.
        any_doc = search(backend, {"size": 1, "sort": [{backend.field_time: "desc"}],
                                   "query": {"match_all": {}}}, args)
        sources = hits_of(any_doc)
        print(f"no document matches {backend.field_instance} = {instance}.")
        if not sources:
            fail(f"and index {backend.index} is empty for this query.", EXIT_NODATA)
        print("A document of this index, to check the field names against:")
    flat = OrderedDict()

    def walk(node, path=""):
        if isinstance(node, dict):
            for key, value in node.items():
                walk(value, f"{path}.{key}" if path else key)
        elif isinstance(node, list):
            flat[path] = f"[{len(node)} item(s)]"
        else:
            flat[path] = node

    walk(sources[0])
    if args.json:
        json.dump({"instance": instance, "fields": {k: clean(v, 200) for k, v in flat.items()}},
                  sys.stdout, ensure_ascii=False, indent=2)
        print()
        return
    table("FIELDS OF ONE DOCUMENT", ["Field", "Value"],
          [[k, clean(v, 90)] for k, v in flat.items()], [60, 90])
    print(f"\nSettings now in use ({backend.prefix}FIELD_*): instance={backend.field_instance} "
          f"({backend.instance_match}), level={backend.field_level}, pod={backend.field_pod}, "
          f"message={backend.field_message}, logger={backend.field_logger}")
    print("A field is aggregatable only as a keyword: if the index maps it as text, the setting "
          "must name its .keyword sub-field.")


def cmd_tail(args):
    env = resolve_env(args.env)
    backend = Backend(env)
    require(backend)
    query, instance = build_query(backend, args, env)
    body = {"size": args.limit, "sort": [{backend.field_time: "desc"}], "track_total_hits": True,
            "query": query}
    if not args.full:
        body["_source"] = [backend.field_time, backend.field_level, backend.field_message,
                           backend.field_pod, backend.field_logger]
    result = search(backend, body, args)
    sources = hits_of(result)
    total, capped = total_of(result)
    rows = [row_of(backend, s) for s in reversed(sources)]

    def printer():
        header(args, instance, total, capped)
        if not rows:
            print("no line matches.")
            return
        for row in rows:
            pod = f" [{row['pod'].rsplit('-', 2)[-1]}]" if args.show_pod else ""
            print(f"{row['time']} {row['level']:<5}{pod} {row['message']}")
        if args.full:
            print("\n--- full source of the newest line ---")
            print(clean_block(json.dumps(sources[0], ensure_ascii=False, indent=2)))

    emit(args, {"instance": instance, "total": total, "total_is_lower_bound": capped,
                "lines": rows}, printer)
    if not rows:
        sys.exit(EXIT_NODATA)


def cmd_errors(args):
    env = resolve_env(args.env)
    backend = Backend(env)
    require(backend)
    levels = list(LEVELS_BAD) + (["WARN", "WARNING"] if args.warn else [])
    query, instance = build_query(backend, args, env, levels=levels)
    result = search(backend, {"size": args.scan, "sort": [{backend.field_time: "desc"}],
                              "track_total_hits": True, "query": query,
                              "_source": [backend.field_time, backend.field_level,
                                          backend.field_message, backend.field_pod,
                                          backend.field_logger]}, args)
    sources = hits_of(result)
    total, capped = total_of(result)
    groups = OrderedDict()
    for source in sources:
        row = row_of(backend, source)
        group = groups.setdefault(signature(dig(source, backend.field_message)), {
            "count": 0, "level": row["level"], "logger": row["logger"],
            "first": row["time"], "last": row["time"], "pods": set(), "sample": row["message"]})
        group["count"] += 1
        group["first"] = min(group["first"], row["time"])
        group["last"] = max(group["last"], row["time"])
        group["pods"].add(row["pod"])
    ranked = sorted(groups.items(), key=lambda kv: -kv[1]["count"])[: args.top]
    rows = [{"signature": sig, "count": g["count"], "level": g["level"], "logger": g["logger"],
             "first": g["first"], "last": g["last"], "pods": sorted(g["pods"]),
             "sample": g["sample"]} for sig, g in ranked]

    def printer():
        header(args, instance, total, capped,
               f"scanned {len(sources)}, {len(groups)} distinct")
        if not rows:
            print("no error in this window.")
            return
        table("ERRORS BY MESSAGE SHAPE", ["Count", "Level", "First", "Last", "Pods", "Message"],
              [[r["count"], r["level"], r["first"][5:], r["last"][5:], len(r["pods"]),
                r["sample"]] for r in rows], [7, 5, 14, 14, 5, 110])
        if capped or len(sources) < total:
            print(f"\n  only the {len(sources)} newest of {total}{'+' if capped else ''} lines were "
                  f"grouped (--scan N for more).")

    emit(args, {"instance": instance, "total": total, "total_is_lower_bound": capped,
                "scanned": len(sources), "distinct": len(groups), "groups": rows}, printer)
    if not rows:
        sys.exit(EXIT_NODATA)


def cmd_summary(args):
    env = resolve_env(args.env)
    backend = Backend(env)
    require(backend)
    query, instance = build_query(backend, args, env)
    aggs = {
        "levels": {"terms": {"field": backend.field_level, "size": 10}},
        "pods": {"terms": {"field": backend.field_pod, "size": 20}},
        "loggers": {"terms": {"field": backend.field_logger, "size": 10}},
        "timeline": {"auto_date_histogram": {"field": backend.field_time, "buckets": args.buckets}},
    }
    result = search(backend, {"size": 0, "track_total_hits": True, "query": query, "aggs": aggs}, args)
    total, capped = total_of(result)
    got = result.get("aggregations", {})
    buckets = lambda name: [(clean(b["key_as_string" if "key_as_string" in b else "key"]),
                             b["doc_count"]) for b in got.get(name, {}).get("buckets", [])]
    levels, pods, loggers, timeline = (buckets(n) for n in ("levels", "pods", "loggers", "timeline"))
    bad = sum(c for level, c in levels if level.upper() in LEVELS_BAD)
    # An unmapped or text field aggregates to nothing instead of failing: say so, or "no error"
    # would be read as good news.
    blind = [name for name, rows, field in
             (("level", levels, backend.field_level), ("pod", pods, backend.field_pod),
              ("logger", loggers, backend.field_logger)) if total and not rows]

    # The top errors come from the same window, so the summary answers "what is failing" on its own.
    error_groups = []
    if bad:
        equery, _ = build_query(backend, args, env, levels=list(LEVELS_BAD))
        eresult = search(backend, {"size": min(args.scan, 500), "query": equery,
                                   "sort": [{backend.field_time: "desc"}],
                                   "_source": [backend.field_time, backend.field_message,
                                               backend.field_logger]}, args)
        counter = Counter()
        samples, last = {}, {}
        for source in hits_of(eresult):
            sig = signature(dig(source, backend.field_message))
            counter[sig] += 1
            samples.setdefault(sig, clean(dig(source, backend.field_message)))
            last.setdefault(sig, short_time(dig(source, backend.field_time)))
        error_groups = [{"count": c, "last": last[s], "sample": samples[s]}
                        for s, c in counter.most_common(args.top)]

    def printer():
        health = f"{bad} error line(s)" if bad else \
            ("error count unknown" if "level" in blind else "no error")
        header(args, instance, total, capped, health)
        for name in blind:
            field = {"level": backend.field_level, "pod": backend.field_pod,
                     "logger": backend.field_logger}[name]
            print(f"  ! {name}: field '{field}' grouped nothing although there are lines. It is "
                  f"probably unmapped or text; run `fields {args.service} --env {args.env}` and "
                  f"set {backend.prefix}FIELD_{name.upper()} (often the .keyword sub-field).")
        if not total:
            print("no line in this window: wrong environment, wrong service name, or the service "
                  "is silent. Check with `fields`.")
            return
        width = max((c for _, c in timeline), default=1)
        print("\nTIMELINE")
        for when, count in timeline:
            bar = "#" * max(1, round(20 * count / width)) if count else ""
            print(f"  {when[:16].replace('T', ' ')}  {count:>7}  {bar}")
        table("LEVELS", ["Level", "Lines"], [[l, c] for l, c in levels], [12, 10])
        table("PODS", ["Pod", "Lines"], [[p, c] for p, c in pods], [70, 10])
        table("TOP LOGGERS", ["Logger", "Lines"], [[l, c] for l, c in loggers], [70, 10])
        if error_groups:
            table("TOP ERRORS", ["Count", "Last", "Message"],
                  [[g["count"], g["last"][5:], g["sample"]] for g in error_groups], [7, 14, 110])

    emit(args, {"instance": instance, "total": total, "total_is_lower_bound": capped,
                "error_lines": bad, "fields_that_grouped_nothing": blind,
                "levels": dict(levels), "pods": dict(pods),
                "loggers": dict(loggers), "timeline": [{"at": w, "count": c} for w, c in timeline],
                "top_errors": error_groups}, printer)
    if not total:
        sys.exit(EXIT_NODATA)


def cmd_pods(args):
    env = resolve_env(args.env)
    backend = Backend(env)
    require(backend)
    query, instance = build_query(backend, args, env)
    aggs = {"pods": {"terms": {"field": backend.field_pod, "size": args.top},
                     "aggs": {"last": {"max": {"field": backend.field_time}},
                              "sample": {"top_hits": {"size": 1, "_source": [backend.field_image],
                                                      "sort": [{backend.field_time: "desc"}]}}}}}
    result = search(backend, {"size": 0, "track_total_hits": True, "query": query, "aggs": aggs}, args)
    total, capped = total_of(result)
    rows = []
    for bucket in result.get("aggregations", {}).get("pods", {}).get("buckets", []):
        hits = bucket.get("sample", {}).get("hits", {}).get("hits", [])
        source = hits[0].get("_source", {}) if hits else {}
        rows.append({"pod": clean(bucket["key"]), "lines": bucket["doc_count"],
                     "last": short_time(bucket.get("last", {}).get("value_as_string")),
                     "image": clean(dig(source, backend.field_image))})

    def printer():
        header(args, instance, total, capped)
        if total and not rows:
            print(f"  ! field '{backend.field_pod}' grouped nothing although there are lines: set "
                  f"{backend.prefix}FIELD_POD (often the .keyword sub-field) — see `fields`.")
        table("PODS", ["Pod", "Lines", "Last line", "Image"],
              [[r["pod"], r["lines"], r["last"], r["image"]] for r in rows], [60, 10, 20, 80])

    emit(args, {"instance": instance, "total": total, "pods": rows}, printer)
    if not rows:
        sys.exit(EXIT_NODATA)


# ------------------------------------------------------------------- command line

def main():
    parser = argparse.ArgumentParser(
        prog="app_logs.py", description="Read and analyse a service's logs, read-only.")
    sub = parser.add_subparsers(dest="command", required=True)

    def common(p, service=True):
        if service:
            p.add_argument("service", help="service name; the instance is built from it")
        p.add_argument("--env", "-e", required=True, help="staging, uat, production, ...")
        p.add_argument("--since", "--from", dest="since", default="now-1h",
                       help="start, date-math or ISO 8601 (default now-1h)")
        p.add_argument("--until", "--to", dest="until", default="now", help="end (default now)")
        p.add_argument("--pod", default="", help="only this pod (a * wildcard is allowed)")
        p.add_argument("--query", "-q", default="", help="phrase that the message must contain")
        p.add_argument("--logger", default="", help="only this logger/class")
        p.add_argument("--include-noise", action="store_true",
                       help="keep the health-check lines that are dropped by default")
        p.add_argument("--timeout", type=float, default=30.0, help="seconds (default 30)")
        p.add_argument("--json", action="store_true", help="JSON instead of text")

    p = sub.add_parser("doctor", help="settings and reachability")
    p.add_argument("--env", "-e", default="", help="only this environment")
    p.add_argument("--timeout", type=float, default=30.0)
    p.set_defaults(func=cmd_doctor)

    p = sub.add_parser("fields", help="fields of a sample document")
    p.add_argument("service")
    p.add_argument("--env", "-e", required=True)
    p.add_argument("--timeout", type=float, default=30.0)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_fields)

    p = sub.add_parser("tail", help="the most recent lines")
    common(p)
    p.add_argument("--level", default="", help="only this level")
    p.add_argument("--limit", "-n", type=int, default=50, help="lines (default 50)")
    p.add_argument("--show-pod", action="store_true", help="add the pod suffix to each line")
    p.add_argument("--full", action="store_true", help="also dump the newest document in full")
    p.set_defaults(func=cmd_tail)

    p = sub.add_parser("errors", help="errors grouped by message shape")
    common(p)
    p.add_argument("--warn", action="store_true", help="group warnings too")
    p.add_argument("--scan", type=int, default=500, help="lines to group (default 500)")
    p.add_argument("--top", type=int, default=15, help="groups to print (default 15)")
    p.set_defaults(func=cmd_errors)

    p = sub.add_parser("summary", help="levels, pods, timeline and top errors")
    common(p)
    p.add_argument("--buckets", type=int, default=12, help="timeline buckets (default 12)")
    p.add_argument("--scan", type=int, default=500, help="error lines to group (default 500)")
    p.add_argument("--top", type=int, default=8, help="error groups to print (default 8)")
    p.set_defaults(func=cmd_summary)

    p = sub.add_parser("pods", help="pods that logged, with their image")
    common(p)
    p.add_argument("--top", type=int, default=20, help="pods to print (default 20)")
    p.set_defaults(func=cmd_pods)

    args = parser.parse_args()
    for name in ("limit", "scan", "top", "buckets"):
        value = getattr(args, name, None)
        if value is not None and not 1 <= value <= 5000:
            fail(f"--{name} must be between 1 and 5000", EXIT_USAGE)
    if getattr(args, "service", "") and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,80}",
                                                         args.service):
        fail(f"service name {args.service!r} is not a name", EXIT_USAGE)
    args.func(args)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
    except BrokenPipeError:
        os._exit(0)
