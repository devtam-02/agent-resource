#!/usr/bin/env python3
"""Which environments override the config keys a pull request changes.

For each key, reads the service's configuration on each environment through app-config-review's
app_config.py (`show --json`, secrets already masked) and reports whether the environment sets the
key itself ("ghi đè", with its shown value) or leaves the packaged value in force ("—"). Keys are
matched with Spring's relaxed binding (app.kafka.topic == APP_KAFKA_TOPIC), and a YAML leaf name
(group-id) matches every full key that ends with it.

Read-only. Needs the app-config-review plugin and its settings (APP_CONFIG_*).

  env_overrides.py --service svc --keys spring.kafka.consumer.auto-offset-reset,app.kafka.topic
  env_overrides.py --service svc --scan scan.json --envs staging,uat,production --format md

Settings: SPRING_PR_REVIEW_APP_CONFIG = path of app_config.py when it cannot be found.
Exit codes: 0 ok, 2 usage, 3 app_config.py not found.
"""

import argparse
import glob
import json
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from plugin_settings import setting  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN_ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
APP_CONFIG_REL = os.path.join("app-config-review", "skills", "app-config-access", "scripts", "app_config.py")
PACKAGED_CONFIG_RE = re.compile(r"(^|/)(application|bootstrap)([-.][\w-]+)?\.(ya?ml|properties)$", re.I)
VALUE_WIDTH = 60


def find_app_config():
    configured, _ = setting("SPRING_PR_REVIEW_APP_CONFIG")
    candidates = [os.path.expanduser(configured)] if configured else []
    candidates.append(os.path.join(os.path.dirname(PLUGIN_ROOT), APP_CONFIG_REL))
    home = os.path.expanduser("~")
    candidates += sorted(glob.glob(os.path.join(home, ".claude", "dev-cli", "marketplace", "plugins", APP_CONFIG_REL)))
    candidates += sorted(glob.glob(os.path.join(home, ".claude", "plugins", "**", "app-config-access", "scripts",
                                                "app_config.py"), recursive=True), reverse=True)
    for path in candidates:
        if path and os.path.isfile(path):
            return path
    return None


def canonical(key):
    """Spring relaxed binding: case, dashes, dots and underscores do not matter."""
    return key.lower().replace("-", "").replace("_", ".")


def keys_from_scan(path):
    with open(path, encoding="utf-8") as fh:
        scan = json.load(fh)
    keys = []
    for change in scan.get("config_changes", []):
        if PACKAGED_CONFIG_RE.search(change.get("file", "")) and change["key"] not in keys:
            keys.append(change["key"])
    return keys


def load_env(app_config, service, env, fetch):
    command = [sys.executable, app_config, "show", service, "--env", env, "--json"]
    if not fetch:
        command.append("--no-fetch")
    proc = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600)
    if proc.returncode != 0:
        first = (proc.stderr or "").strip().splitlines()[:1]
        return None, first[0] if first else f"exit {proc.returncode}"
    try:
        data = json.loads(proc.stdout)
    except ValueError:
        return None, "app_config.py trả về dữ liệu không phải JSON"
    entries = {}
    for file_name, values in (data.get("files") or {}).items():
        for key, value in (values or {}).items():
            entries.setdefault(key, (file_name, value))
    return {"commit": data.get("commit"), "entries": entries}, None


def short(value):
    text = re.sub(r"\s+", " ", str(value if value is not None else "")).strip()
    return text if len(text) <= VALUE_WIDTH else text[: VALUE_WIDTH - 1] + "…"


def match(entries, key):
    want = canonical(key)
    exact = [(k, v) for k, v in entries.items() if canonical(k) == want]
    if exact:
        return exact
    if "." not in key:  # YAML leaf name from the diff: match the full keys that end with it
        return [(k, v) for k, v in entries.items() if canonical(k).endswith("." + want)]
    return []


def overrides(app_config, service, envs, keys, fetch=True):
    loaded = {}
    errors = {}
    for env in envs:
        data, error = load_env(app_config, service, env, fetch)
        if error:
            errors[env] = error
        else:
            loaded[env] = data
    rows = []
    for key in keys:
        row = {"key": key, "envs": {}}
        for env in envs:
            if env in errors:
                row["envs"][env] = {"status": "lỗi đọc"}
                continue
            hits = match(loaded[env]["entries"], key)
            if not hits:
                row["envs"][env] = {"status": "—"}
            else:
                row["envs"][env] = {"status": "ghi đè",
                                    "values": [{"key": k, "file": f, "value": short(v)} for k, (f, v) in hits]}
        rows.append(row)
    return {"service": service, "environments": envs,
            "commits": {env: loaded[env]["commit"] for env in loaded}, "errors": errors, "keys": rows}


def render_md(result):
    envs = result["environments"]
    lines = ["| Key | " + " | ".join(envs) + " |", "|---|" + "---|" * len(envs)]
    for row in result["keys"]:
        cells = []
        for env in envs:
            cell = row["envs"][env]
            if cell["status"] == "ghi đè":
                parts = [(f"`{v['key']}`=" if v["key"] != row["key"] else "") + f"`{v['value']}`"
                         for v in cell["values"]]
                cells.append("ghi đè: " + ", ".join(parts).replace("|", "\\|"))
            else:
                cells.append(cell["status"])
        lines.append(f"| `{row['key']}` | " + " | ".join(cells) + " |")
    for env, error in result["errors"].items():
        lines.append(f"\n- {env}: không đọc được cấu hình ({error})")
    return "\n".join(lines) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description="Which environments override the config keys a PR changes.")
    parser.add_argument("--service", required=True, help="service folder name in the config repositories")
    parser.add_argument("--envs", default="staging,uat,production")
    parser.add_argument("--keys", default="", help="comma-separated keys")
    parser.add_argument("--scan", help="pr_scan.py JSON output: its packaged config keys are added")
    parser.add_argument("--no-fetch", action="store_true", help="use the cached config clones")
    parser.add_argument("--format", choices=["json", "md"], default="json")
    args = parser.parse_args(argv)

    keys = [k.strip() for k in args.keys.split(",") if k.strip()]
    if args.scan:
        try:
            keys += [k for k in keys_from_scan(args.scan) if k not in keys]
        except (OSError, ValueError) as exc:
            print(f"env_overrides: không đọc được {args.scan}: {exc}", file=sys.stderr)
            return 2
    if not keys:
        print("env_overrides: không có key nào (dùng --keys hoặc --scan)", file=sys.stderr)
        return 2
    app_config = find_app_config()
    if not app_config:
        print("env_overrides: không tìm thấy app_config.py của plugin app-config-review; cài plugin đó hoặc khai "
              "SPRING_PR_REVIEW_APP_CONFIG", file=sys.stderr)
        return 3
    envs = [e.strip() for e in args.envs.split(",") if e.strip()]
    result = overrides(app_config, args.service, envs, keys, fetch=not args.no_fetch)
    if args.format == "md":
        sys.stdout.write(render_md(result))
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
