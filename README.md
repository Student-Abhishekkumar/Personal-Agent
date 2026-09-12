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

## What you need

- **Python 3.11+** (developed on 3.13)
- **Ollama** running (`ollama serve` / the desktop app) with:
  - a **tools-capable chat model**, e.g. `ollama pull qwen3:4b` (the default).
    Models without the `tools` capability (e.g. `ornith-1.5:9b`) cannot do
    reliable tool calling — the agent will fail with output-parse retries.
  - optionally a **vision model** for desktop sight, e.g. `ornith-1.5:9b`
    (it has `vision`; used by `look_at_screen`)
- `pip install pydantic-ai bs4`
- optional: `pip install pyautogui` — desktop vision + mouse/keyboard control

## Features

### Tools (25)
`now` · `calculator` · `web_fetch` · `web_search` · `read_file` · `write_file`
· `append_file` · `list_directory` · `search_files` · `run_command` ·
`list_skills` · `get_skill` · `save_skill` · `remember` · `forget` ·
`search_notes` · `list_notes` · `screenshot` · `look_at_screen` ·
`mouse_move` · `mouse_click` · `mouse_drag` · `mouse_scroll` · `type_text` ·
`press_key`

The model picks the tools itself from their schemas. `run_command` executes
shell commands in the workspace; file tools resolve paths relative to it.
`remember`/`search_notes` give it a persistent long-term memory
(`.agent/notes.json`). The calculator is a safe, whitelisted evaluator — no
code execution.

**Vision + desktop control** (needs `pip install pyautogui`): `look_at_screen`
sends a screenshot to a vision model (default `ornith-1.5:9b`, override with
`OLLAMA_VISION_MODEL`) so the agent can literally see windows, buttons and
dialogs, then act with the mouse/keyboard tools. Screenshots land in
`.agent/screenshots/`. Mouse control includes a failsafe: slamming the mouse
into the top-left corner aborts it.

The loop *look → act → look again* works end to end (proven in
`tests/test_vision_notepad.py`, where the agent typed a typo-ridden sentence
into Notepad, spotted its own typos via vision, and physically fixed them):

```powershell
python agent.py "Open Notepad, type 'agent test', then look at the screen and tell me exactly what it says."
```

Expect each `look_at_screen` to take ~30–90 s (vision model inference on a
1080p screenshot) — and don't touch the mouse/keyboard while the agent drives.

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

### Tests (`tests/`)

Runnable proofs, no pytest — just run them. Desktop tests need
`pip install pyautogui` and will physically move your mouse/keyboard.

| Script | What it demonstrates |
|---|---|
| `test_win_key.py` | Full Win-key pipeline: Win+R → `notepad` → typed sentence → screenshot → surgical cleanup (kills only the PID it spawned). |
| `compare_prog.py` | Same job via the file API + `Start-Process`: ~95 ms vs 9.6 s, verified window title. |
| `verify_followup.py` | Confirms no stray text leaked to disk or Notepad's session cache; retry-loop title check. |
| `diag2.py` | Window-ownership forensics + Notepad `TabState` cache inspection. |
| `test_chrome_search.py` | The agent's real chrome search path (no LLM): headless Google→Bing, live results in ~8 s. |
| `test_open_apps.py` | Opens Word (programmatic) and Brave (Win-key with programmatic fallback); verifies window titles. |
| `test_vision.py` | `look_at_screen`: screenshot → vision model describes the screen. |
| `test_vision_notepad.py` | Full computer-use loop: physically type a typo-ridden line in Notepad → vision reads it → vision proposes the fix → physically applied → vision verifies. |

Measured lessons (Windows 11): the GUI path took **9.6 s** vs **~0.1 s**
programmatic; Win11 Notepad silently reopens the previous session's tabs (the
Win+R path types into a *restored* document unless you make a new tab first);
the Run dialog pre-fills its last command (select-all before typing); and
models without Ollama's `tools` capability fail agent tool-calls with
output-parse retries. Prefer API/CLI/COM/UIA; reserve keystroke automation for
apps that offer no other interface. Test screenshots are git-ignored (they
capture the desktop).

## REPL commands

```
/help     /new     /tools     /skills     /skill <name>
/notes    /model <name>     /history     /exit
```

## Configuration

| Flag | Env var | Default |
|------|---------|---------|
| `--model` | `OLLAMA_MODEL` | `qwen3:4b` |
| `--base-url` | `OLLAMA_BASE_URL` | `http://localhost:11434/v1` |
| `--search-backend` | `SEARCH_BACKEND` | `auto` (`auto`/`brave`/`chrome`) |
| — | `OLLAMA_VISION_MODEL` | `ornith-1.5:9b` (used by `look_at_screen`) |
| — | `BRAVE_API_KEY` | *(unset — Brave path off)* |
| — | `CHROME_PATH` | *(auto-detected)* |
| `--workspace` | — | this folder |
| `--data-dir` | — | `./.agent` |
| `--skills-dir` | — | `./skills` |

Other flags: `--list-tools`, `--list-skills`, `--print-system-prompt`,
`--request-limit N`, `--no-history`.

Tip: set `PYDANTIC_AI_NO_BANNER=1` to silence the pydantic-ai startup banner.