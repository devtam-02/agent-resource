# telegram-send

Claude Code plugin that sends **one local file** to the Telegram group configured in dev-cli.
dev-cli does the sending, so the bot token never reaches the plugin.

| Piece | What it is |
|---|---|
| Skill `send` — `/telegram-send:send [file]` | The normal path. Resolves which file is meant, runs the script, reports. Frontmatter sets `effort: low`: a send needs no deliberation. |
| Agent `telegram-sender` | The same send on **`model: haiku`, `effort: low`**. Delegate to it when the main conversation is on an expensive model and should not spend tokens on the upload. |
| Script `skills/send/scripts/send_telegram.py` | `doctor`, `check <file>` (prints what would be sent, sends nothing), `send <file> --yes [--topic N] [--force]`. |

Claude also loads the skill on its own for matching requests.

## Which one to use

The agent is **not** automatically cheaper. A subagent pays for its own system prompt, so for a
single file the skill on the current model usually costs less in total. The agent wins when the
conversation would otherwise carry the upload in an expensive context: a long session on Opus, a
batch of files, or a send at the end of a large task whose context is already big.

Both end at the same script and the same guards.

## Setup

Settings come from dev-cli's **`~/.dev-cli/.env`** (`DEV_CLI_HOME` moves the folder). The bot token
and the chat id are dev-cli's own keys, written by `dev-cli --send-telegram --config`:

```bash
TELEGRAM_BOT_TOKEN=<set by dev-cli>
TELEGRAM_CHAT_ID=<set by dev-cli>
# TELEGRAM_TOPIC_ID=<default topic of a forum group>
# TELEGRAM_SEND_DEV_CLI=dev-cli          # path to dev-cli when it is not on PATH
```

Check with:

```bash
python3 skills/send/scripts/send_telegram.py doctor
```

It prints whether each key is set, never its value; the chat id is shown masked (`-100…547`).

## Guards

The script refuses, **before anything leaves the machine**, when the file does not exist, is a
directory, is empty, is over 50 MB (Telegram's document limit), or has a name that says it holds a
credential (`.env`, `*.pem`, `*.key`, `id_rsa`, `*credentials*`, `.npmrc`, ...). The last one is
overridable with `--force`, which the skill may only use when the user asks for that file knowing
its contents will sit in the group chat.

`--yes` is required for every send: the flag exists so that an accidental run cannot upload
anything. Without it the script prints what it would do and exits 2.

Exit codes: 0 sent, 2 not confirmed or bad argument, 3 not configured, 4 the file was refused,
5 the send failed; dev-cli's own codes are passed through.

Requirements: `python3`, `dev-cli` with Telegram configured. Python 3 standard library only.
