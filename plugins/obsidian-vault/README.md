# obsidian-vault

Claude Code plugin that reads, writes and syncs an Obsidian vault. Reading and writing work on the
files, so Obsidian does not have to be running; syncing asks the running Obsidian app to run the
Git Vault Sync community plugin, which commits and
pushes the vault. Skills and agents only.

| Piece | What it does |
|---|---|
| Skill `read` — `/obsidian-vault:read <words or note>` | `search` (names and content, `--folder`, `--tag`, `--prop`, `--regex`), `list` (`--recent`), `read` (whole note, `--section`, `--outline`, `--props`), `links` (outgoing and backlinks), `daily`. |
| Skill `write` — `/obsidian-vault:write <note>` | `write` creates a note (refuses an existing one unless `--overwrite`, which backs the old one up), `append` to the end or under a heading (`--create` for a missing note or heading), `prop` sets, adds to, replaces or removes properties. Rename, move and delete go through Obsidian's CLI so links are updated. |
| Skill `sync` — `/obsidian-vault:sync` | `sync` runs the sync command in Obsidian and waits for the commit and the push; `status` shows pending changes, ahead/behind and merge conflicts. `effort: low`. |
| Agent `vault-reader` | `model: haiku`, `effort: low`. Searches and reads the notes that answer one question and replies with `[[note#section]]` citations, so long notes stay out of the main conversation. |
| Agent `vault-syncer` | `model: haiku`, `effort: low`. Runs `sync` and reports, for when the main conversation should not wait for it. |

The code-review plugin (`changes` skill) writes its reports into the same vault with its own script;
it reads `OBSIDIAN_VAULT`, `OBSIDIAN_CLI` and `OBSIDIAN_VAULT_NAME` too.

## Setup

Settings live in dev-cli's **`~/.dev-cli/.env`** (`DEV_CLI_HOME` moves the folder; a process
environment variable with the same name wins):

```bash
OBSIDIAN_VAULT=/path/to/vault                 # the folder holding .obsidian/ (required)
# OBSIDIAN_VAULT_NAME=<name>                  # as Obsidian lists it; default: the folder name
# OBSIDIAN_INBOX=Inbox                        # folder for a new note given without a folder; default: vault root
# OBSIDIAN_CLI=obsidian                       # Obsidian's command line (Settings > General > Command line interface)
# OBSIDIAN_SYNC_COMMAND=git-vault-sync:sync   # Obsidian command id that syncs the vault
# OBSIDIAN_SYNC_TIMEOUT=90                    # seconds to wait for the commit and the push
# OBSIDIAN_AUTO_SYNC=off                      # on: sync after every write the agent makes
# OBSIDIAN_BACKUP_DIR=~/.cache/obsidian-vault/backups   # old versions kept by write --overwrite
```

Check with `python3 scripts/vault.py doctor`. It also warns when the sync plugin's settings file
(`.obsidian/plugins/git-vault-sync/data.json`, which holds its GitHub token) is not in the vault's
`.gitignore`: the plugin excludes it from its own commits, but a plain `git add -A` would not.

## Script

```bash
V=scripts/vault.py
python3 $V search "keycloak" --folder learning
python3 $V read oauth-basics --outline
python3 $V read learning/oauth-basics --section "Scopes"
python3 $V write "Inbox/2026-11-17 họp sprint" --file /tmp/note.md --prop status=draft
python3 $V append "Inbox/2026-11-17 họp sprint" --text "- [ ] chốt scope" --section "Việc cần làm" --create
python3 $V prop "Inbox/2026-11-17 họp sprint" --set status=done --add tags=sprint
python3 $V status
python3 $V sync
```

A note is a vault-relative path (`.md` may be left out) or a bare name resolved like a wikilink; an
ambiguous name lists the candidates. `.obsidian/`, `.git/` and `.trash/` are never read or written,
and a path that leaves the vault (`..`, a symbolic link) is refused. New names must be portable: no
`\ : * ? " < > | # ^ [ ]`, no leading dot. Writes are atomic and use `\n` line endings.

Sync waits for every change except the plugin's `excludePaths` (such as `workspace.json`) to be
committed, then for the branch to be level with its upstream. It refuses to start while the vault
has merge conflicts.

Exit codes: 0 ok, 2 usage or bad name, 3 vault not configured, 4 note not found, ambiguous or already
exists, 5 Obsidian not running or no CLI, 6 sync not committed or pushed in time, 7 merge conflicts.

Requirements: `python3` (standard library only), `git`; for sync, the Obsidian app with its command line enabled
and the Git Vault Sync plugin. Tests: `python3 -m unittest discover -s tests`.
