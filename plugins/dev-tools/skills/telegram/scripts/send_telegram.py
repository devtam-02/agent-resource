#!/usr/bin/env python3
"""Send one local file to the configured Telegram group through dev-cli.

dev-cli does the sending (`dev-cli --send-telegram -f --path <file>`): it holds the bot token and
the chat id in ~/.dev-cli/.env and never prints them. This script only checks the file before
anything leaves the machine, so the model does not have to reason about it:

  - the path exists, is a regular file, is readable and is not empty
  - it is not larger than Telegram accepts for a document
  - it does not look like a secret (.env, a key, a credentials file) unless --force is given
  - --yes is required: a send is an external side effect and cannot be undone

Commands:
  doctor                      dev-cli found, Telegram keys present (values never printed)
  check PATH                  what would be sent; nothing leaves the machine
  send PATH --yes [--topic N] [--force]          a file, through dev-cli --send-telegram --path
  message --yes [--text T | --stdin] [--topic N] [--force]
                              a text message, through dev-cli --send-telegram --message -
                              (the text goes through stdin, never through the command line)

Exit codes: 0 ok, 2 usage / not confirmed, 3 not configured, 4 file refused, 5 send failed;
other dev-cli codes are passed through. Python 3 standard library only.
"""

import argparse
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

EXIT_USAGE, EXIT_CONFIG, EXIT_FILE, EXIT_SEND = 2, 3, 4, 5

#: Telegram refuses a document larger than this through the bot API.
MAX_BYTES = 50 * 1024 * 1024

#: Files whose name alone says they hold a credential. Sending one to a group chat leaks it to
#: everyone in the group and to Telegram's servers, so it needs --force.
SECRET_NAMES = re.compile(
    r"(^|[._-])(env|envrc|secrets?|credentials?|password[s]?|token[s]?|netrc|htpasswd)($|[._-])"
    r"|^\.env|^id_(rsa|dsa|ecdsa|ed25519)$|^\.?(npmrc|pypirc|git-credentials)$",
    re.IGNORECASE)
SECRET_SUFFIXES = {".pem", ".key", ".p12", ".pfx", ".jks", ".keystore", ".kdbx", ".ppk", ".asc"}

#: Text that looks like a credential. A message is not a file, but it lands in the same group.
TEXT_SECRET_RES = (
    (re.compile(r"\b\d{8,10}:[A-Za-z0-9_-]{30,}\b"), "a Telegram bot token"),
    (re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}|\bgh[pousr]_[A-Za-z0-9]{30,}"), "a GitHub token"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "an AWS access key id"),
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"), "a private key"),
    (re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"), "a JWT"),
    (re.compile(r"(?i)\b(password|passwd|pwd|secret|api[-_]?key|token)\b\s*[:=]\s*\S{4,}"),
     "a value after password/secret/token"),
    (re.compile(r"\b[a-z][a-z0-9+.-]*://[^\s/:@]+:[^\s/@]+@"), "a URL with user:password"),
)

#: Telegram's limit for one text message.
MAX_MESSAGE_CHARS = 4096

CONTROL_RE = re.compile(
    r"\x1b\[[0-9;?]*[ -/]*[@-~]"
    r"|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)?"
    r"|\x1b[@-_]?"
    r"|[\x00-\x08\x0b-\x1f\x7f\x80-\x9f]"
)

DEV_CLI_HINTS = {
    2: "dev-cli refused the arguments.",
    3: "Telegram is not configured. The user runs: ! dev-cli --send-telegram --config",
    4: "dev-cli could not read the file.",
    5: "Network error reaching Telegram.",
    6: "Telegram refused the upload (file too large, bot removed from the group, wrong chat id).",
    130: "Interrupted.",
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


# ------------------------------------------------------------------- helpers

def fail(message, code):
    print(f"send_telegram: {message}", file=sys.stderr)
    sys.exit(code)


def clean(value, width=None):
    text = CONTROL_RE.sub("", str(value).replace("\r", " ").replace("\n", " ").replace("\t", " "))
    text = re.sub(r" {2,}", " ", text).strip() or "-"
    return text[: width - 1] + "…" if width and len(text) > width else text


def dev_cli_exe():
    exe = setting("TELEGRAM_SEND_DEV_CLI", "dev-cli")[0]
    found = shutil.which(exe) or (exe if Path(exe).is_file() and os.access(exe, os.X_OK) else "")
    if not found:
        fail(f"dev-cli not found ({exe!r}). Install it, or set TELEGRAM_SEND_DEV_CLI in "
             f"{dev_cli_env_path()} to its path.", EXIT_CONFIG)
    return found


def human(size):
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024


def mask_chat(value):
    """A chat id is not a secret, but it identifies a private group: show only its ends."""
    text = clean(value)
    return text if len(text) <= 6 else f"{text[:4]}…{text[-3:]}"


def looks_secret(path):
    """-> a reason, or "" when the name says nothing alarming."""
    name = path.name
    if path.suffix.lower() in SECRET_SUFFIXES:
        return f"the extension {path.suffix} is used for keys and certificates"
    if SECRET_NAMES.search(name):
        return "the file name is the kind used for credentials"
    return ""


def inspect(path_text, force):
    """Check the file. Nothing is sent here; every refusal happens before any upload."""
    try:
        path = Path(path_text).expanduser().resolve(strict=True)
    except FileNotFoundError:
        fail(f"no such file: {path_text}", EXIT_FILE)
    except OSError as exc:
        fail(f"cannot read the path {path_text}: {exc}", EXIT_FILE)
    if path.is_dir():
        fail(f"{path} is a directory. Telegram takes one file: archive the folder first.", EXIT_FILE)
    if not path.is_file():
        fail(f"{path} is not a regular file.", EXIT_FILE)
    if not os.access(path, os.R_OK):
        fail(f"{path} is not readable.", EXIT_FILE)
    size = path.stat().st_size
    if size == 0:
        fail(f"{path} is empty: nothing to send.", EXIT_FILE)
    if size > MAX_BYTES:
        fail(f"{path} is {human(size)}; Telegram refuses a document over {human(MAX_BYTES)}.",
             EXIT_FILE)
    reason = looks_secret(path)
    if reason and not force:
        fail(f"refusing {path.name}: {reason}. Sending it puts its contents in the group chat and "
             f"on Telegram's servers. Pass --force only if the user, knowing this, asks for it.",
             EXIT_FILE)
    return path, size, reason


# ------------------------------------------------------------------- commands

def cmd_doctor(args):
    print(f"settings file: {dev_cli_env_path()}"
          f"{'' if dev_cli_env_path().is_file() else '  (missing)'}")
    exe = dev_cli_exe()
    print(f"dev-cli:       {exe}")
    token, token_src = setting("TELEGRAM_BOT_TOKEN")
    chat, chat_src = setting("TELEGRAM_CHAT_ID")
    print(f"bot token:     {'set (' + token_src + ')' if token else 'MISSING'}")
    print(f"chat id:       {mask_chat(chat) + ' (' + chat_src + ')' if chat else 'MISSING'}")
    topic = setting("TELEGRAM_TOPIC_ID")[0]
    if topic:
        print(f"topic id:      {clean(topic)}")
    if not token or not chat:
        fail("Telegram is not configured. The user runs: ! dev-cli --send-telegram --config",
             EXIT_CONFIG)


def cmd_check(args):
    path, size, reason = inspect(args.path, force=True)
    chat = setting("TELEGRAM_CHAT_ID")[0]
    print(f"file:    {path}")
    print(f"size:    {human(size)}")
    print(f"to:      Telegram group {mask_chat(chat) if chat else '(not configured)'}"
          f"{', topic ' + str(args.topic) if args.topic else ''}")
    if reason:
        print(f"WARNING: {reason}. Sending it puts its contents in the group chat. "
              f"Ask the user before using --force.")
    print("nothing was sent: this is `check`. Add `send ... --yes` once the user has confirmed.")


def text_secret(text):
    """-> the kind of credential the text seems to contain, or "" when none is seen."""
    for pattern, label in TEXT_SECRET_RES:
        if pattern.search(text):
            return label
    return ""


def cmd_message(args):
    if not args.yes:
        fail("nothing was sent. A send cannot be undone, so it needs --yes, and --yes belongs "
             "there only after the user has asked for this exact text to be sent.", EXIT_USAGE)
    if args.stdin:
        text = sys.stdin.read()
    else:
        text = args.text or ""
    body = text.replace("\r\n", "\n").strip()
    if not body:
        fail("the message is empty. Nothing was sent.", EXIT_USAGE)
    if len(body) > MAX_MESSAGE_CHARS:
        fail(f"the message is {len(body)} characters; Telegram accepts at most "
             f"{MAX_MESSAGE_CHARS}. Nothing was sent.", EXIT_FILE)
    kind = text_secret(body)
    if kind and not args.force:
        fail(f"refusing to send: the text looks like {kind}. Sending it puts it in the group "
             f"chat. Pass --force only if the user, knowing this, asks for it.", EXIT_FILE)
    exe = dev_cli_exe()
    command = [exe, "--send-telegram", "--message", "-", "-f"]
    if args.topic:
        command += ["--topic", str(args.topic)]
    chat = setting("TELEGRAM_CHAT_ID")[0]
    print(f"sending a message of {len(body)} characters to the Telegram group "
          f"{mask_chat(chat) if chat else '(see dev-cli)'}"
          f"{', topic ' + str(args.topic) if args.topic else ''}...")
    if kind:
        print(f"(--force: the text looks like {kind})")
    try:
        done = subprocess.run(command, input=body.encode("utf-8"), check=False)
    except OSError as exc:
        fail(f"could not run dev-cli: {exc}", EXIT_SEND)
    if done.returncode == 0:
        print("sent.")
        return
    hint = DEV_CLI_HINTS.get(done.returncode, "")
    fail(f"dev-cli exited {done.returncode}, the message was not confirmed as sent."
         f"{' ' + hint if hint else ''}", done.returncode)


def cmd_send(args):
    if not args.yes:
        fail("nothing was sent. A send cannot be undone, so it needs --yes, and --yes belongs "
             "there only after the user has asked for this exact file to be sent.", EXIT_USAGE)
    path, size, reason = inspect(args.path, force=args.force)
    exe = dev_cli_exe()
    command = [exe, "--send-telegram", "-f", "--path", str(path)]
    if args.topic:
        command += ["--topic", str(args.topic)]
    chat = setting("TELEGRAM_CHAT_ID")[0]
    print(f"sending {path.name} ({human(size)}) to the Telegram group "
          f"{mask_chat(chat) if chat else '(see dev-cli)'}"
          f"{', topic ' + str(args.topic) if args.topic else ''}...")
    if reason:
        print(f"(--force: {reason})")
    try:
        done = subprocess.run(command, check=False)
    except OSError as exc:
        fail(f"could not run dev-cli: {exc}", EXIT_SEND)
    if done.returncode == 0:
        print("sent.")
        return
    hint = DEV_CLI_HINTS.get(done.returncode, "")
    fail(f"dev-cli exited {done.returncode}, the file was not confirmed as sent."
         f"{' ' + hint if hint else ''}", done.returncode)


def main():
    parser = argparse.ArgumentParser(
        prog="send_telegram.py",
        description="Send one local file to the configured Telegram group, through dev-cli.")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("doctor", help="dev-cli and the Telegram keys")
    p.set_defaults(func=cmd_doctor)

    p = sub.add_parser("check", help="what would be sent; sends nothing")
    p.add_argument("path")
    p.add_argument("--topic", type=int, default=0, help="topic id of a forum group")
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("send", help="send the file")
    p.add_argument("path")
    p.add_argument("--yes", action="store_true", help="required: the user asked for this send")
    p.add_argument("--topic", type=int, default=0, help="topic id of a forum group")
    p.add_argument("--force", action="store_true",
                   help="send although the name looks like a credential file")
    p.set_defaults(func=cmd_send)

    p = sub.add_parser("message", help="send a text message (no file)")
    source = p.add_mutually_exclusive_group(required=True)
    source.add_argument("--text", help="the message text (keep it short; quote it)")
    source.add_argument("--stdin", action="store_true", help="read the message from standard input")
    p.add_argument("--yes", action="store_true", help="required: the user asked for this send")
    p.add_argument("--topic", type=int, default=0, help="topic id of a forum group")
    p.add_argument("--force", action="store_true",
                   help="send although the text looks like a credential")
    p.set_defaults(func=cmd_message)

    args = parser.parse_args()
    if getattr(args, "topic", 0) and not 1 <= args.topic <= 10**12:
        fail("--topic is not a topic id", EXIT_USAGE)
    args.func(args)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
    except BrokenPipeError:
        os._exit(0)
