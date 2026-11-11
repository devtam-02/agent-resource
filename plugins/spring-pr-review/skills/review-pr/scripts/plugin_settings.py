"""Settings reader shared by the spring-pr-review scripts.

Settings live in dev-cli's file ~/.dev-cli/.env ($DEV_CLI_HOME moves the folder), shared by dev-cli
and every agent-resource plugin. A process environment variable with the same name wins, for one-off
runs. Same parsing rules as dev-cli: KEY=value, full-line # comments, optional single or double
quotes, no other processing. Copied from app-config-review's app_config.py (README: "Settings").
"""

import os
from pathlib import Path


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
