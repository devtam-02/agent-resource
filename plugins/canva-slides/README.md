# canva-slides

Claude Code plugin to build presentations: a checked Markdown outline first, then a Canva deck
through Canva's official MCP server, then a PDF/PPTX export. Skills only, no legacy `commands/`.

| Component | What it does |
|---|---|
| skill `slides` — `/canva-slides:slides [topic\|files] [--type ...] [--minutes N] [--lang vi\|en] [--outline-only]` | Brief → outline with speaker notes (`slides/<date>-<slug>.md`) → `outline_check.py` → your confirmation → Canva `generate-design` → you pick a candidate → content checked against the outline → optional export. Sources: a topic, Markdown specs/ADRs, a `dev-cli --jira-tool scan-issues` JSON export, a git range. Claude also loads it on its own for slide requests. References: `deck-types.md` (sprint-review, tech-design, tool-intro, incident, knowledge, status), `writing.md`, `outline-template.md`, `canva-flow.md`. |
| skill `export` — `/canva-slides:export [name\|link\|id] [pdf\|pptx\|png] [path]` | Finds a Canva design, exports it and downloads the file. |
| MCP server `canva` (`.mcp.json`) | `https://mcp.canva.com/mcp`, HTTP with OAuth. Nothing runs locally. |

## Connect Canva once

After the plugin is installed (`dev-cli --agent-resource init`, then `/reload-plugins` or a new
session):

1. `/mcp` → the Canva server → **Authenticate**.
2. Log in to Canva in the browser and allow access. Claude Code keeps the OAuth token; this
   repository holds no credential.

Which Canva server you see: when your claude.ai account also has the Canva connector, Claude Code
hides the plugin's server because it points at the same URL (debug log: "Lazy dedup: suppressing
1 plugin server(s) that duplicate claude.ai connectors: plugin:canva-slides:canva", Claude Code
2.1.316). Then authenticate `claude.ai Canva` instead. Without that connector,
`plugin:canva-slides:canva` is listed. The skills use whichever one is connected.

Plan limits come from Canva: generation, editing, export and comments work on any account; resize
needs Pro; brand kits, brand templates and autofill need Enterprise; Free exports at standard
quality.

## Outline checker

```bash
python3 skills/slides/scripts/outline_check.py slides/deck.md            # findings, exit 1 on errors
python3 skills/slides/scripts/outline_check.py --json slides/deck.md     # findings + parsed slides
python3 skills/slides/scripts/outline_check.py --prompt slides/deck.md   # Canva prompt, refused on errors
```

It checks the front matter, slide numbering, bullets per slide and their length, pace against
`duration_min`, missing-data placeholders, and **sensitive data** (tokens, credentials, private
keys, email addresses, phone numbers, card numbers, national id numbers, private IPs, internal
host names). Sensitive findings are errors and are reported by kind and line only, never by value.
Python 3 standard library only.

## Data leaving the machine

Everything sent to the Canva server (the outline text, and anything you ask to upload) is processed
by Canva under its [privacy policy](https://www.canva.com/policies/privacy-policy/). The `slides`
skill asks before the first call that sends content, and refuses to build the prompt while the
checker reports sensitive data. Use `--outline-only` for internal material.
