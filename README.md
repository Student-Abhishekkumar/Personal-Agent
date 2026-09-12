# Personal Agent

A capable, local, tool-using AI agent built on **pydantic-ai 2.x** + **Ollama**.
Extends the original `agent.py` stub with three things: **tools**, **skills**
and **history**.

## Quick start

```powershell
# interactive chat
python agent.py

# one-shot question
python agent.py "what files are in this project?"

# start a brand-new conversation
python agent.py --new "hello, call me Ada"

# no persistence
python agent.py --no-history "one-off question"
```

## Features

### Tools (17)
`now` · `calculator` · `web_fetch` · `web_search` · `read_file` · `write_file`
· `append_file` · `list_directory` · `search_files` · `run_command` ·
`list_skills` · `get_skill` · `save_skill` · `remember` · `forget` ·
`search_notes` · `list_notes`

The model picks the tools itself from their schemas. `run_command` executes
shell commands in the workspace; file tools resolve paths relative to it.
`remember`/`search_notes` give it a persistent long-term memory
(`.agent/notes.json`). The calculator is a safe, whitelisted evaluator — no
code execution.

### Web search — Brave or Chrome (no DuckDuckGo)

`web_search` uses, in this order:

1. **Brave Search API** — used automatically when `BRAVE_API_KEY` is set
   (free key at <https://brave.com/search/api/>). Fast, no browser needed.
2. **Chrome/Edge rendering** — otherwise the agent drives your installed
   Chrome (or Edge) in headless mode via `--dump-dom` (no Selenium needed) to
   render a real **Google** search, falling back to **Bing** if Google blocks
   it. This uses a throwaway browser profile; nothing is stored.

```powershell
$env:BRAVE_API_KEY = "YOUR_KEY_HERE"
python agent.py --search-backend brave "latest python release"

python agent.py --search-backend chrome "today's news"   # force the browser path
```

### Skills
Reusable procedure bundles stored as Markdown files in `skills/`, each with
frontmatter (`description`, `when_to_use`). The catalog is injected into the
system prompt, so the agent knows when to load one (`get_skill`) — and it can
author new ones mid-conversation (`save_skill`). Two starters are included:
`code-review` and `troubleshooting`.

```powershell
python agent.py --list-skills     # see the catalog
```

Skill file format:

```markdown
---
name: my-skill
description: One-line summary.
when_to_use: When the agent should apply it.
---

1. Step-by-step instructions...
```

### History
Every turn is saved to `.agent/history.json` (lossless pydantic-ai message
serialization) and reloaded on the next run, so conversations survive restarts
— in the REPL and one-shot mode alike.

```powershell
python agent.py --new           # archive history.json and start fresh
python agent.py /new            # same, from inside the REPL
python agent.py --max-history 20
```

### Experiments: GUI automation vs programmatic control (`tests/`)

Runnable proofs (no pytest — just run them) that simulated keystrokes are a
last resort next to programmatic control. `test_win_key.py` needs one
optional dependency:

```powershell
pip install pyautogui
python tests\test_win_key.py   # hands off the keyboard for ~8 s!
```

| Script | What it demonstrates |
|---|---|
| `test_win_key.py` | Full Win-key pipeline: Win+R → `notepad` → typed sentence → screenshot → surgical cleanup (kills only the PID it spawned). |
| `compare_prog.py` | Same job via the file API + `Start-Process`: ~95 ms vs 9.6 s, verified window title. |
| `verify_followup.py` | Confirms no stray text leaked to disk or Notepad's session cache; retry-loop title check. |
| `diag2.py` | Window-ownership forensics + Notepad `TabState` cache inspection. |

Measured on Windows 11: the GUI path took **9.6 s** vs **~0.1 s** programmatic,
and Notepad silently reopened the previous session's tabs — the typed text
landed in an *unrelated restored document*. Lesson: prefer API/CLI/COM/UIA;
reserve `pyautogui`-style keystroke automation for apps that offer no other
interface. Test screenshots are git-ignored (they capture the desktop).

## REPL commands

## REPL commands

```
/help     /new     /tools     /skills     /skill <name>
/notes    /model <name>     /history     /exit
```

## Configuration

| Flag | Env var | Default |
|------|---------|---------|
| `--model` | `OLLAMA_MODEL` | `ornith-1.5:9b` |
| `--base-url` | `OLLAMA_BASE_URL` | `http://localhost:11434/v1` |
| `--search-backend` | `SEARCH_BACKEND` | `auto` (`auto`/`brave`/`chrome`) |
| — | `BRAVE_API_KEY` | *(unset — Brave path off)* |
| — | `CHROME_PATH` | *(auto-detected)* |
| `--workspace` | — | this folder |
| `--data-dir` | — | `./.agent` |
| `--skills-dir` | — | `./skills` |

Other flags: `--list-tools`, `--list-skills`, `--print-system-prompt`,
`--request-limit N`, `--no-history`.

Tip: set `PYDANTIC_AI_NO_BANNER=1` to silence the pydantic-ai startup banner.