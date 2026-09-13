"""Agent — a capable, local, tool-using AI agent built on pydantic-ai + Ollama.

It ships with:

* **Tools**  — current date/time, safe calculator, file read/write/append,
  directory listing, grep-style file search, shell command execution,
  web fetch, web search (Brave API or Chrome/Edge-rendered Google/Bing),
  skill management (list/get/save), and
  persistent long-term notes (remember/forget/search/list).
* **Skills** — reusable instruction bundles stored as Markdown files in
  ``./skills``. The agent is told about available skills in its system prompt
  and can load one by name (``get_skill``) or author a new one (``save_skill``).
* **History** — the conversation is persisted to ``.agent/history.json`` and
  reloaded on the next run, so the agent remembers earlier sessions.

Usage
-----
    python agent.py                                # interactive REPL
    python agent.py "add 12 and 15"                # one-shot question
    python agent.py --new "start fresh"            # archive history, begin anew
    python agent.py --list-tools                   # show registered tools
    python agent.py --list-skills                  # show available skills
"""

from __future__ import annotations

import argparse
import ast
import base64
import html as _html
import json
import math
import operator as _op
import os
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional, Sequence

from pydantic_ai import Agent
from pydantic_ai.messages import ModelMessage, ModelMessagesTypeAdapter
from pydantic_ai.models.openai import OpenAIChatModelSettings
from pydantic_ai.models.ollama import OllamaModel
from pydantic_ai.providers.ollama import OllamaProvider
from pydantic_ai.usage import UsageLimits

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

AGENT_NAME = "Personal Agent"

# Default chat model (user preference: ornith-1.5:9b). NOTE: ornith does not
# advertise Ollama's "tools" capability, so complex tool-calling can fail with
# output-parse retries; qwen3:4b (--model qwen3:4b) is the tools-capable
# alternative and is also faster on tool-heavy turns.
DEFAULT_MODEL = os.environ.get("OLLAMA_MODEL", "ornith-1.5:9b")
DEFAULT_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434/v1")

PROJECT_DIR = Path(__file__).resolve().parent
DEFAULT_DATA_DIR = PROJECT_DIR / ".agent"
DEFAULT_SKILLS_DIR = PROJECT_DIR / "skills"

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; local-personal-agent)"
MAX_TOOL_OUTPUT = 16_000  # characters, cap for tool results


@dataclass
class Settings:
    """Runtime settings for the agent."""

    model: str = DEFAULT_MODEL
    base_url: str = DEFAULT_BASE_URL
    workspace: Path = PROJECT_DIR
    data_dir: Path = DEFAULT_DATA_DIR
    skills_dir: Path = DEFAULT_SKILLS_DIR
    request_limit: int = 100
    max_history: int = 40
    persist: bool = True
    search_backend: str = "auto"  # "auto" | "brave" | "chrome"
    fast: bool = False  # voice mode: essential tools only -> much faster turns

    @property
    def history_file(self) -> Path:
        return self.data_dir / "history.json"

    @property
    def notes_file(self) -> Path:
        return self.data_dir / "notes.json"


# Replaced in main(); used by tool functions at call time.
_current_settings = Settings()
# ---------------------------------------------------------------------------
# Skills — Markdown bundles with frontmatter, living in ./skills
# ---------------------------------------------------------------------------


def _parse_frontmatter(text: str) -> dict[str, str]:
    """Extract YAML-ish ``---`` frontmatter (name/description/when_to_use)."""
    meta: dict[str, str] = {}
    m = re.match(r"\A---\s*\n(.*?)\n---\s*\n?", text, re.S)
    if m:
        for line in m.group(1).splitlines():
            key, _, value = line.partition(":")
            if value:
                meta[key.strip().lower()] = value.strip()
    return meta


def _strip_frontmatter(text: str) -> str:
    return re.sub(r"\A---\s*\n.*?\n---\s*\n?", "", text, flags=re.S)


class SkillManager:
    """Reads/writes skills as Markdown files under a directory."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    @staticmethod
    def safe_name(name: str) -> str:
        name = re.sub(r"[^A-Za-z0-9_.\-]+", "-", name.strip())
        if not name or name in {".", ".."} or "/" in name or "\\" in name:
            raise ValueError(f"invalid skill name: {name!r}")
        return name

    def _skill_path(self, name: str) -> Path:
        return self.root / f"{self.safe_name(name)}.md"

    def list_skills(self) -> list[dict[str, str]]:
        if not self.root.is_dir():
            return []
        items: list[dict[str, str]] = []
        for f in sorted(self.root.glob("*.md")):
            meta = _parse_frontmatter(f.read_text(encoding="utf-8", errors="replace"))
            items.append(
                {
                    "name": f.stem,
                    "description": meta.get("description", ""),
                    "when_to_use": meta.get("when_to_use", ""),
                    "file": str(f),
                }
            )
        return items

    def get_skill(self, name: str) -> Optional[str]:
        p = self._skill_path(name)
        if not p.exists():
            return None
        return _strip_frontmatter(p.read_text(encoding="utf-8", errors="replace")).strip()

    def save_skill(self, name: str, description: str, when_to_use: str, content: str) -> Path:
        safe = self.safe_name(name)
        self.root.mkdir(parents=True, exist_ok=True)
        text = (
            f"---\n"
            f"name: {safe}\n"
            f"description: {description.strip()}\n"
            f"when_to_use: {when_to_use.strip()}\n"
            f"---\n\n"
            f"{content.strip()}\n"
        )
        p = self.root / f"{safe}.md"
        p.write_text(text, encoding="utf-8")
        return p

    def catalog_prompt(self) -> str:
        items = self.list_skills()
        if not items:
            return "(no skills yet — create one with the save_skill tool)"
        return "\n".join(
            f"- {i['name']}: {i['description']}  (use when: {i['when_to_use']})"
            for i in items
        )
# ---------------------------------------------------------------------------
# Conversation history & long-term notes persistence
# ---------------------------------------------------------------------------


def load_history(settings: Settings) -> list[ModelMessage]:
    if not settings.history_file.exists():
        return []
    try:
        raw = ModelMessagesTypeAdapter.validate_json(settings.history_file.read_bytes())
    except Exception:
        return []
    return list(raw)


def save_history(settings: Settings, messages: Sequence[ModelMessage]) -> Path:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    payload = ModelMessagesTypeAdapter.dump_json(list(messages))
    settings.history_file.write_bytes(payload)
    return settings.history_file


def archive_history(settings: Settings) -> None:
    """Move the current history file aside (used by --new and /new)."""
    f = settings.history_file
    if not f.exists():
        return
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dst = settings.data_dir / f"history.{stamp}.json"
    f.rename(dst)
    print(f"[history archived to {dst.name}]")


def _load_notes(settings: Settings) -> dict[str, str]:
    if not settings.notes_file.exists():
        return {}
    try:
        return json.loads(settings.notes_file.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_notes(settings: Settings, notes: dict[str, str]) -> None:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.notes_file.write_text(
        json.dumps(notes, ensure_ascii=False, indent=2), encoding="utf-8"
    )


# ---------------------------------------------------------------------------
# Internal helpers for the tools
# ---------------------------------------------------------------------------


def _resolve_path(path: str, base: Optional[Path] = None) -> Path:
    p = Path(path).expanduser()
    if not p.is_absolute():
        p = (base or _current_settings.workspace) / p
    return p.resolve()


def _trim(text: str, limit: int = MAX_TOOL_OUTPUT) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n… [truncated, {len(text) - limit} chars omitted]"


def html_to_text(raw: str, max_chars: int = 8000) -> str:
    """Turn an HTML page into collapsed plain text."""
    try:
        from bs4 import BeautifulSoup  # type: ignore

        soup = BeautifulSoup(raw, "html.parser")
        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()
        text = soup.get_text(" ")
    except ImportError:
        text = re.sub(r"(?is)<(script|style).*?</\1>", " ", raw)
        text = re.sub(r"(?is)<[^>]+>", " ", text)
    text = _html.unescape(text)
    text = re.sub(r"[ \t\r\n\u00a0]+", " ", text).strip()
    return text[:max_chars]


def _fetch_url(url: str, timeout: float = 20.0) -> tuple[str, str]:
    """Fetch a URL; returns (text, error) — exactly one is non-empty."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            charset = resp.headers.get_content_charset() or "utf-8"
    except Exception as exc:
        return "", f"Could not fetch {url!r}: {exc}"
    try:
        text = raw.decode(charset, "replace")
    except LookupError:
        text = raw.decode("utf-8", "replace")
    return text, ""


def _browser_exes() -> list[Path]:
    """Locate an installable Chrome/Edge binary (CHROME_PATH wins first)."""
    explicit = os.environ.get("CHROME_PATH", "").strip()
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit))
    candidates += [
        PROJECT_DIR / "chrome.exe",
        Path("C:/Program Files/Google/Chrome/Application/chrome.exe"),
        Path("C:/Program Files (x86)/Google/Chrome/Application/chrome.exe"),
        Path(os.environ.get("LOCALAPPDATA", "")) / "Google/Chrome/Application/chrome.exe",
        Path("C:/Program Files/Microsoft/Edge/Application/msedge.exe"),
        Path("C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"),
    ]
    seen: set[str] = set()
    found: list[Path] = []
    for p in candidates:
        key = str(p).lower()
        if not p or key in seen or not p.exists():
            continue
        seen.add(key)
        found.append(p)
    return found


def _chrome_dom(url: str, timeout: int = 35) -> tuple[str, str]:
    """Render ``url`` with headless Chrome/Edge and return the final DOM.

    Uses ``subprocess`` only (no Selenium needed). Returns
    (dom_or_empty, error_or_empty).
    """
    profile = tempfile.mkdtemp(prefix="agent_chrome_")
    flags = [
        "--headless=new",
        "--disable-gpu",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-extensions",
        "--virtual-time-budget=10000",
        f"--user-data-dir={profile}",
        "--dump-dom",
    ]
    try:
        for exe in _browser_exes():
            try:
                proc = subprocess.run(
                    [str(exe), *flags, url],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=timeout,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
            except (OSError, subprocess.TimeoutExpired):
                continue
            if proc.returncode == 0 and proc.stdout.strip():
                return proc.stdout, ""
    finally:
        shutil.rmtree(profile, ignore_errors=True)
    return "", "no Chrome/Edge browser available to render search results"


def _search_brave(query: str, max_results: int) -> list[tuple[str, str, str]]:
    """Search via the Brave Search API (requires BRAVE_API_KEY)."""
    key = os.environ.get("BRAVE_API_KEY", "").strip()
    if not key:
        return []
    params = urllib.parse.urlencode({"q": query, "count": max_results})
    req = urllib.request.Request(
        "https://api.search.brave.com/res/v1/web/search?" + params,
        headers={"X-Subscription-Token": key, "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
    except Exception:
        return []
    results: list[tuple[str, str, str]] = []
    for item in data.get("web", {}).get("results", [])[:max_results]:
        title = str(item.get("title", "")).strip()
        url = str(item.get("url", "")).strip()
        desc = str(item.get("description", "")).strip()
        if title and url:
            results.append((title, url, desc))
    return results
def _parse_google_page(page: str, max_results: int) -> list[tuple[str, str, str]]:
    """Extract results from a (headless-rendered) Google SERP."""
    try:
        from bs4 import BeautifulSoup  # type: ignore
    except ImportError:
        return []
    soup = BeautifulSoup(page, "html.parser")
    results: list[tuple[str, str, str]] = []
    blocks = soup.select("div.g") or [
        d for d in soup.find_all("div", attrs={"data-hveid": True})
    ]
    for block in blocks:
        a = block.find("a", href=True)
        h = block.find(["h3", "h2"])
        if a is None or h is None:
            continue
        url = a["href"]
        if not url.startswith("http"):
            continue
        title = h.get_text(" ", strip=True)
        if not title:
            continue
        sn = block.find(class_=re.compile(r"VwiC3b|snippet"))
        snippet = sn.get_text(" ", strip=True) if sn else ""
        if not snippet:
            for div in block.find_all("div"):
                txt = div.get_text(" ", strip=True)
                if txt and txt != title and len(txt) > 40:
                    snippet = txt
                    break
        results.append((title, url, snippet))
        if len(results) >= max_results:
            break
    if not results:
        for a in soup.find_all("a", href=True):
            h = a.find(["h3", "h2"])
            if h is None:
                continue
            url = a["href"]
            if not url.startswith("http") or "google." in url:
                continue
            results.append((h.get_text(" ", strip=True), url, ""))
            if len(results) >= max_results:
                break
    return results


def _decode_bing_url(url: str) -> str:
    """De-obfuscate a bing.com/ck/a redirect link into the real URL."""
    if "bing.com/ck/a" not in url:
        return url
    try:
        q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        token = (q.get("u") or q.get("url") or [None])[0]
        if not token:
            return url
        token = token.replace(".", "=")
        token += "=" * (-len(token) % 4)
        decoded = base64.urlsafe_b64decode(token)
        return decoded.decode("utf-8", "replace")
    except Exception:
        return url


def _parse_bing_page(page: str, max_results: int) -> list[tuple[str, str, str]]:
    """Extract results from a (headless-rendered) Bing SERP."""
    try:
        from bs4 import BeautifulSoup  # type: ignore
    except ImportError:
        return []
    soup = BeautifulSoup(page, "html.parser")
    results: list[tuple[str, str, str]] = []
    for li in soup.select("li.b_algo"):
        a = li.find("a", href=True)
        h = li.find(["h2", "h3"])
        if a is None or h is None:
            continue
        url = _decode_bing_url(a["href"])
        if not url.startswith("http"):
            continue
        cap = li.find(class_=re.compile(r"b_caption|b_lineclamp|b_snippet"))
        snippet = cap.get_text(" ", strip=True) if cap else ""
        results.append((h.get_text(" ", strip=True), url, snippet))
        if len(results) >= max_results:
            break
    return results


def _search_bing_rss(query: str, max_results: int) -> list[tuple[str, str, str]]:
    """Search Bing via its plain-XML RSS endpoint (no browser needed)."""
    import xml.etree.ElementTree as ET

    params = urllib.parse.urlencode({"q": query, "format": "rss"})
    req = urllib.request.Request(
        "https://www.bing.com/search?" + params,
        headers={"User-Agent": USER_AGENT},
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            xml = resp.read().decode("utf-8", "replace")
    except Exception:
        return []
    results: list[tuple[str, str, str]] = []
    try:
        root = ET.fromstring(xml)
        for item in root.iter("item"):
            title = (item.findtext("title") or "").strip()
            url = (item.findtext("link") or "").strip()
            desc = (item.findtext("description") or "").strip()
            desc = _html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", desc))).strip()
            if title and url.startswith("http"):
                results.append((title, url, desc))
            if len(results) >= max_results:
                break
    except Exception:
        return []
    return results


def _search_chrome(query: str, max_results: int) -> tuple[list[tuple[str, str, str]], str]:
    """Search using Google/Bing via Chrome, with Bing RSS as a fast fallback.

    Order: Google (headless Chrome) → Bing RSS (plain XML) → Bing HTML
    (headless Chrome). Returns (results, engine_label).
    """
    q = urllib.parse.quote_plus(query)

    dom, err = _chrome_dom(f"https://www.google.com/search?q={q}&num={max_results}")
    if dom and not err:
        results = _parse_google_page(dom, max_results)
        if results:
            return results, "Google (headless Chrome)"

    rss = _search_bing_rss(query, max_results)
    if rss:
        return rss, "Bing"

    dom, err = _chrome_dom(f"https://www.bing.com/search?q={q}")
    if dom and not err:
        results = _parse_bing_page(dom, max_results)
        if results:
            return results, "Bing (headless Edge/Chrome)"

    return [], ""


def _format_results(label: str, results: list[tuple[str, str, str]]) -> str:
    lines = [f"Search results via {label}:"]
    for i, (title, url, snippet) in enumerate(results, 1):
        lines.append(f"{i}. {title}")
        lines.append(f"   {url}")
        if snippet:
            lines.append(f"   {snippet}")
    return "\n".join(lines)


_MATH_BINDINGS: dict[str, Any] = {
    **{n: getattr(math, n) for n in (
        "sqrt", "sin", "cos", "tan", "asin", "acos", "atan", "log", "log10",
        "log2", "exp", "floor", "ceil", "radians", "degrees", "pow",
        "factorial",
    )},
    "abs": abs,
    "round": round,
    "pi": math.pi,
    "e": math.e,
    "tau": math.tau,
}

_OPS: dict[type, Callable[[Any, Any], Any]] = {
    ast.Add: _op.add,
    ast.Sub: _op.sub,
    ast.Mult: _op.mul,
    ast.Div: _op.truediv,
    ast.FloorDiv: _op.floordiv,
    ast.Mod: _op.mod,
    ast.Pow: _op.pow,
    ast.USub: _op.neg,
    ast.UAdd: _op.pos,
}


def _safe_eval(node: ast.AST) -> Any:
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.BinOp):
        fn = _OPS.get(type(node.op))
        if fn is None:
            raise ValueError(f"operator not allowed: {type(node.op).__name__}")
        return fn(_safe_eval(node.left), _safe_eval(node.right))
    if isinstance(node, ast.UnaryOp):
        fn = _OPS.get(type(node.op))
        if fn is None:
            raise ValueError(f"operator not allowed: {type(node.op).__name__}")
        return fn(_safe_eval(node.operand))
    if isinstance(node, ast.Name):
        if node.id in _MATH_BINDINGS:
            return _MATH_BINDINGS[node.id]
        raise ValueError(f"name not allowed: {node.id!r}")
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        fn = _MATH_BINDINGS.get(node.func.id)
        if not callable(fn):
            raise ValueError(f"function not allowed: {node.func.id!r}")
        return fn(*[_safe_eval(a) for a in node.args])
    raise ValueError(f"expression element not allowed: {type(node).__name__}")
# ---------------------------------------------------------------------------
# Tools — every function in TOOLS is registered as a callable tool.
# ---------------------------------------------------------------------------


def now() -> str:
    """Get the current date and local time.

    Very important: use this whenever you need to know what time/date it is
    right now, or when computing ages, deadlines or weekdays.

    Returns:
        The current local date and time as an ISO-8601 string.
    """
    return datetime.now().astimezone().isoformat(timespec="seconds")


def calculator(expression: str) -> str:
    """Safely evaluate a math expression (never executes arbitrary code).

    Supports + - * / // % ** parentheses, numbers, and these functions:
    sqrt, sin, cos, tan, log, log10, log2, exp, abs, floor, ceil, pow,
    factorial, radians, degrees, round, plus constants pi, e and tau.

    Args:
        expression: A math expression, e.g. "round((15 * 3.2 + 2 ** 4) / 7, 2)".

    Returns:
        The numeric result as a string.
    """
    try:
        tree = ast.parse(expression, mode="eval")
        value = _safe_eval(tree.body)
        if isinstance(value, float):
            value = round(value, 10)
        return str(value)
    except Exception as exc:
        return f"Calculator error: {exc}"


def web_fetch(url: str, max_chars: int = 8000) -> str:
    """Fetch a web page and return its readable text content.

    Args:
        url: Full URL, e.g. "https://example.com/page".
        max_chars: Maximum number of characters to return.

    Returns:
        The page rendered as plain text, or an error message.
    """
    text, err = _fetch_url(url)
    if err:
        return err
    return html_to_text(text, max_chars)


def web_search(query: str, max_results: int = 5) -> str:
    """Search the web and return the top results.

    Backends, in order: the Brave Search API (used automatically when the
    BRAVE_API_KEY environment variable is set), otherwise a real Google or
    Bing search rendered through your installed Chrome/Edge browser. No
    DuckDuckGo, no API accounts required for the browser path.

    Args:
        query: Search keywords, e.g. "best python http library 2026".
        max_results: Maximum number of results to show (1-10).

    Returns:
        A numbered list with titles, URLs and snippets.
    """
    max_results = max(1, min(int(max_results), 10))
    backend = _current_settings.search_backend.lower()

    if backend in ("auto", "brave"):
        brave_results = _search_brave(query, max_results)
        if brave_results:
            return _format_results("Brave Search API", brave_results)
        if backend == "brave":
            if not os.environ.get("BRAVE_API_KEY", "").strip():
                return (
                    "Brave backend selected but BRAVE_API_KEY is not set. "
                    "Get a free key at https://brave.com/search/api/ and set "
                    "BRAVE_API_KEY (or use --search-backend chrome)."
                )
            return "Brave Search API returned no results (check your key or rate limit)."

    if backend in ("auto", "chrome"):
        chrome_results, engine = _search_chrome(query, max_results)
        if chrome_results:
            return _format_results(engine, chrome_results)

    return (
        f"Web search failed for {query!r}. Enable it by either:\n"
        "  - setting BRAVE_API_KEY (free at https://brave.com/search/api/) "
        "and using --search-backend brave, or\n"
        "  - installing Chrome or Edge (used automatically by the chrome backend)."
    )


def read_file(path: str, start_line: Optional[int] = None, end_line: Optional[int] = None) -> str:
    """Read a text file, optionally only a line range.

    Args:
        path: File path, absolute or relative to the workspace.
        start_line: First line to read (1-based). Omit to read from the start.
        end_line: Last line to read (inclusive). Omit to read to the end.

    Returns:
        The file content as text.
    """
    try:
        p = _resolve_path(path)
        lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
    except FileNotFoundError:
        return f"File not found: {path!r}"
    except Exception as exc:
        return f"Could not read {path!r}: {exc}"
    if start_line is not None or end_line is not None:
        start = max(0, (start_line or 1) - 1)
        end = end_line if end_line is not None else len(lines)
        selected = lines[start:end]
    else:
        selected = lines
    if not selected:
        return "(empty file)"
    return _trim("\n".join(selected), limit=200_000)


def write_file(path: str, content: str) -> str:
    """Create or overwrite a text file (creates parent directories).

    Args:
        path: Destination path, absolute or relative to the workspace.
        content: Full new file content.

    Returns:
        Confirmation including byte size.
    """
    try:
        p = _resolve_path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return f"Wrote {len(content.encode('utf-8'))} bytes to {p}"
    except Exception as exc:
        return f"Could not write {path!r}: {exc}"


def append_file(path: str, content: str) -> str:
    """Append text to a file, creating it if it does not exist.

    Args:
        path: File path, absolute or relative to the workspace.
        content: Text to append.

    Returns:
        Confirmation.
    """
    try:
        p = _resolve_path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as fh:
            fh.write(content if content.endswith("\n") else content + "\n")
        return f"Appended to {p}"
    except Exception as exc:
        return f"Could not append to {path!r}: {exc}"


def list_directory(path: str = ".") -> str:
    """List the contents of a directory with entry sizes.

    Args:
        path: Directory path, absolute or relative to the workspace.

    Returns:
        One line per entry: type, name, size.
    """
    try:
        p = _resolve_path(path)
        entries = sorted(p.iterdir(), key=lambda x: (x.is_file(), x.name.lower()))
    except FileNotFoundError:
        return f"Directory not found: {path!r}"
    except Exception as exc:
        return f"Could not list {path!r}: {exc}"
    lines: list[str] = [str(p)]
    for e in entries[:200]:
        kind = "[DIR]" if e.is_dir() else "[FILE]"
        try:
            size = e.stat().st_size
        except OSError:
            size = 0
        lines.append(f"{kind}  {e.name}  ({size:,} bytes)")
    if len(entries) > 200:
        lines.append(f"... and {len(entries) - 200} more entries")
    return "\n".join(lines)


_TEXT_EXTS = {
    ".py", ".txt", ".md", ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg",
    ".html", ".css", ".js", ".csv", ".log", ".xml", ".sql", ".env",
}


def search_files(query: str, path: str = ".", max_results: int = 50) -> str:
    """Search for a text pattern inside files (like grep).

    Args:
        query: Regex or plain text to search for.
        path: File or directory to search, relative to the workspace.
        max_results: Maximum number of matches to return.

    Returns:
        Matching entries as file:line: text.
    """
    try:
        pattern = re.compile(query)
    except re.error as exc:
        return f"Invalid pattern: {exc}"
    root = _resolve_path(path)
    if root.is_file():
        candidates = [root]
    elif root.is_dir():
        candidates = [
            f for f in root.rglob("*")
            if f.is_file()
            and f.suffix.lower() in _TEXT_EXTS
            and not any(part.startswith(".") for part in f.relative_to(root).parts)
        ]
    else:
        return f"Path not found: {path!r}"
    hits: list[str] = []
    for f in candidates:
        try:
            lines = f.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for i, line in enumerate(lines, 1):
            if pattern.search(line):
                hits.append(f"{f}:{i}: {line.strip()[:200]}")
                if len(hits) >= max_results:
                    return _trim("\n".join(hits))
    return _trim("\n".join(hits)) if hits else f"No matches for {query!r} in {path!r}."


def run_command(command: str, timeout: int = 60) -> str:
    """Run a shell command in the workspace and capture its output.

    Use this for installing packages, running scripts and tests, git
    operations, file management, and anything else that touches this machine.

    Args:
        command: The shell command to run (Windows CMD syntax).
        timeout: Maximum seconds to wait (default 60).

    Returns:
        Exit code with stdout and stderr, truncated to 16000 chars.
    """
    try:
        proc = subprocess.run(
            command,
            shell=True,
            cwd=str(_current_settings.workspace),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return f"Command timed out after {timeout}s: {command!r}"
    except Exception as exc:
        return f"Could not run command: {exc}"
    out = (proc.stdout or "").strip()
    err = (proc.stderr or "").strip()
    head = (
        f"Command succeeded (code 0): {command!r}"
        if proc.returncode == 0
        else f"Command exited with code {proc.returncode}: {command!r}"
    )
    parts = [head]
    if out:
        parts.append(out)
    if err:
        parts.append("[stderr]\n" + err)
    return _trim("\n\n".join(parts))


def list_skills() -> str:
    """List every skill the agent can use.

    Returns:
        A catalog of skill names with descriptions.
    """
    manager = SkillManager(_current_settings.skills_dir)
    items = manager.list_skills()
    if not items:
        return "No skills available yet. Ask the agent to create one with save_skill."
    lines: list[str] = []
    for i in items:
        lines.append(f"- {i['name']}: {i['description']}")
        if i["when_to_use"]:
            lines.append(f"  use when: {i['when_to_use']}")
    return "\n".join(lines)


def get_skill(name: str) -> str:
    """Load a skill by name and return its step-by-step instructions.

    Call this when a skill applies, then follow the returned instructions.

    Args:
        name: The skill name (shown by list_skills).

    Returns:
        The skill content, or an error with the available skill names.
    """
    manager = SkillManager(_current_settings.skills_dir)
    content = manager.get_skill(name)
    if content is None:
        available = ", ".join(i["name"] for i in manager.list_skills()) or "none"
        return f"No skill named {name!r}. Available: {available}"
    return content


def save_skill(
    name: str,
    content: str,
    description: str = "",
    when_to_use: str = "",
) -> str:
    """Create or update a skill so it can be reused in later conversations.

    A skill is markdown with step-by-step instructions. Keep it focused,
    concrete and actionable so it can be followed exactly.

    Args:
        name: Short skill name, e.g. "code-review".
        content: Markdown body with the procedure.
        description: One-line summary shown in the skill catalog.
        when_to_use: When the agent should apply this skill.

    Returns:
        Confirmation with the saved path.
    """
    manager = SkillManager(_current_settings.skills_dir)
    try:
        p = manager.save_skill(name, description, when_to_use, content)
    except ValueError as exc:
        return f"Could not save skill: {exc}"
    return f"Saved skill {name!r} to {p}."


def remember(key: str, value: str) -> str:
    """Store a fact permanently in the agent's long-term memory.

    Use this for user preferences, decisions, references and anything else
    worth keeping across conversations.

    Args:
        key: Unique keyword for the note (e.g. "user_home_dir").
        value: The fact to remember.

    Returns:
        Confirmation.
    """
    notes = _load_notes(_current_settings)
    notes[key] = value
    _save_notes(_current_settings, notes)
    return f"Remembered note {key!r}."


def forget(key: str) -> str:
    """Delete a note from long-term memory.

    Args:
        key: The note key to remove.

    Returns:
        Confirmation.
    """
    notes = _load_notes(_current_settings)
    if key not in notes:
        return f"No note named {key!r}."
    del notes[key]
    _save_notes(_current_settings, notes)
    return f"Forgot note {key!r}."


def search_notes(query: str) -> str:
    """Search the agent's long-term memory notes.

    Args:
        query: Text to look for inside note keys and values.

    Returns:
        Matching notes.
    """
    notes = _load_notes(_current_settings)
    hits = [
        f"- {k}: {v}"
        for k, v in notes.items()
        if query.lower() in f"{k} {v}".lower()
    ]
    if not hits:
        return f"No notes match {query!r}."
    return "\n".join(hits)


def list_notes() -> str:
    """List all notes currently stored in long-term memory.

    Returns:
        One line per note.
    """
    notes = _load_notes(_current_settings)
    if not notes:
        return "No notes stored yet."
    return "\n".join(f"- {k}: {v}" for k, v in notes.items())


# ---------------------------------------------------------------------------
# Desktop tools — vision (screenshot -> vision model) + physical mouse/keyboard
# ---------------------------------------------------------------------------

DEFAULT_VISION_MODEL = os.environ.get("OLLAMA_VISION_MODEL", "ornith-1.5:9b")

# Optional dependency: everything desktop-related degrades gracefully.
try:
    import pyautogui as _gui

    _gui.FAILSAFE = True  # slam mouse to the top-left corner to abort
except ImportError:  # pragma: no cover
    _gui = None


def _desktop_available() -> str | None:
    if _gui is None:
        return "pyautogui is not installed — run: pip install pyautogui"
    return None


def _screenshot_path() -> Path:
    shot_dir = _current_settings.data_dir / "screenshots"
    shot_dir.mkdir(parents=True, exist_ok=True)
    return shot_dir / f"screenshot-{datetime.now().strftime('%Y%m%d-%H%M%S')}.png"


def _ollama_vision(image_path: Path, prompt: str) -> str:
    """Ask the vision model about an image via Ollama's native chat API."""
    b64 = base64.b64encode(image_path.read_bytes()).decode("ascii")
    payload = json.dumps({
        "model": DEFAULT_VISION_MODEL,
        "stream": False,
        "messages": [
            {"role": "user", "content": prompt, "images": [b64]}
        ],
        "options": {"temperature": 0.1},
    }).encode("utf-8")
    req = urllib.request.Request(
        "http://localhost:11434/api/chat",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=300) as resp:
        data = json.loads(resp.read().decode("utf-8", "replace"))
    return str(data.get("message", {}).get("content", "")).strip()


def screenshot() -> str:
    """Capture the screen and save it as a PNG file.

    Use this before mouse actions to see what is on screen, and after them
    to verify what changed.

    Returns:
        The path of the saved screenshot, or an error message.
    """
    err = _desktop_available()
    if err:
        return err
    path = _screenshot_path()
    img = _gui.screenshot()
    img.save(path)
    return f"Screenshot saved to {path} (size {img.size[0]}x{img.size[1]})."


def look_at_screen(question: str = "Describe what is currently on the screen.") -> str:
    """Take a screenshot and let the agent actually SEE it (vision model).

    Use this to read windows, buttons, dialogs, error messages or app state
    before deciding on mouse/keyboard actions.

    Args:
        question: What to look for or answer about the screen.

    Returns:
        The vision model's answer about the screenshot.
    """
    err = _desktop_available()
    if err:
        return err
    path = _screenshot_path()
    _gui.screenshot().save(path)
    try:
        answer = _ollama_vision(
            path, f"{question}\n\nAnswer concisely. Mention window titles, "
            "visible buttons and anything relevant to the question."
        )
    except Exception as exc:
        return f"Vision failed ({exc}); screenshot saved at {path}."
    return f"Screenshot: {path}\nVision says: {answer}"


def mouse_move(x: int, y: int) -> str:
    """Move the mouse pointer to screen coordinates (x, y).

    Args:
        x: Horizontal position in pixels (0 is the left edge).
        y: Vertical position in pixels (0 is the top edge).

    Returns:
        Confirmation with the new position.
    """
    err = _desktop_available()
    if err:
        return err
    _gui.moveTo(int(x), int(y), duration=0.25)
    return f"Mouse moved to ({x}, {y})."


def mouse_click(x: int, y: int, button: str = "left", clicks: int = 1) -> str:
    """Move the mouse to (x, y) and click there.

    Args:
        x: Horizontal position in pixels.
        y: Vertical position in pixels.
        button: "left", "right" or "middle".
        clicks: 1 for a single click, 2 for a double-click.

    Returns:
        Confirmation of the action.
    """
    err = _desktop_available()
    if err:
        return err
    _gui.click(int(x), int(y), clicks=int(clicks), button=button)
    return f"Clicked {button} button {clicks}x at ({x}, {y})."


def mouse_drag(x: int, y: int, duration: float = 0.5) -> str:
    """Press and hold the left button, drag to (x, y), then release.

    Args:
        x: Destination horizontal position in pixels.
        y: Destination vertical position in pixels.
        duration: Seconds the drag should take.

    Returns:
        Confirmation of the action.
    """
    err = _desktop_available()
    if err:
        return err
    _gui.dragTo(int(x), int(y), duration=max(0.1, float(duration)), button="left")
    return f"Dragged to ({x}, {y})."


def mouse_scroll(amount: int) -> str:
    """Scroll the mouse wheel.

    Args:
        amount: Positive scrolls up, negative scrolls down (in wheel clicks).

    Returns:
        Confirmation of the action.
    """
    err = _desktop_available()
    if err:
        return err
    _gui.scroll(int(amount))
    return f"Scrolled {amount} wheel clicks."


def type_text(text: str, interval: float = 0.03) -> str:
    """Type text into whatever window currently has focus.

    Make sure to click the right input field first (mouse_click + look_at_screen).

    Args:
        text: The characters to type.
        interval: Seconds between keystrokes (slower = more reliable).

    Returns:
        Confirmation of the action.
    """
    err = _desktop_available()
    if err:
        return err
    _gui.typewrite(text, interval=max(0.0, float(interval)))
    return f"Typed {len(text)} characters."


def press_key(key: str, presses: int = 1) -> str:
    """Press a keyboard key, e.g. "enter", "esc", "tab", "ctrl+c", "win".

    Args:
        key: Key or combo name(s); combos use "+", e.g. "ctrl+s", "win+r".
        presses: How many times to press it.

    Returns:
        Confirmation of the action.
    """
    err = _desktop_available()
    if err:
        return err
    combo = [k.strip().lower() for k in key.split("+") if k.strip()]
    for _ in range(max(1, int(presses))):
        if len(combo) > 1:
            _gui.hotkey(*combo)
        else:
            _gui.press(combo[0])
    return f"Pressed {'+'.join(combo)} x{presses}."


_TTS_ENGINE: Any = None


def _get_tts() -> Any:
    global _TTS_ENGINE
    if _TTS_ENGINE is None:
        import pyttsx3

        _TTS_ENGINE = pyttsx3.init()
        _TTS_ENGINE.setProperty("rate", 185)   # brisk, Jarvis-like
        _TTS_ENGINE.setProperty("volume", 1.0)  # full volume
        # Voice selection: OLLAMA_TTS_VOICE matches part of a voice name.
        # Installed by default: David (US male), Hazel (UK female), Zira (US female).
        wanted = os.environ.get("OLLAMA_TTS_VOICE", "David").lower()
        for v in _TTS_ENGINE.getProperty("voices"):
            if wanted and wanted in v.name.lower():
                _TTS_ENGINE.setProperty("voice", v.id)
                break
    return _TTS_ENGINE


def open_app(name: str) -> str:
    """Launch a Windows application by name and verify its window appeared.

    Use this (not run_command) to open GUI apps — e.g. "notepad", "word",
    "chrome", "brave", "calc", "mspaint", "explorer". The app is started
    detached, so it stays open independently of this agent.

    Args:
        name: App name, e.g. "notepad", "winword", "chrome", "brave", "calc".

    Returns:
        Confirmation with the app's window title, or an error message.
    """
    aliases = {
        "word": "winword", "ms word": "winword", "office": "winword",
        "calculator": "calc", "file explorer": "explorer", "files": "explorer",
        "paint": "mspaint", "edge": "msedge", "google chrome": "chrome",
    }
    app = aliases.get(name.strip().lower(), name.strip())
    query = (
        "$paths = @("
        "\"$env:LOCALAPPDATA\\BraveSoftware\\Brave-Browser\\Application\\brave.exe\","
        "\"$env:ProgramFiles\\Google\\Chrome\\Application\\chrome.exe\","
        "\"$env:ProgramFiles\\Microsoft\\Edge\\Application\\msedge.exe\" ); "
        f"$p = Get-Command '{app}' -ErrorAction SilentlyContinue; "
        "if (-not $p) { foreach ($f in $paths) { if (Test-Path $f) { $p = $f; break } } } "
        f"if (-not $p) {{ $ap = Get-ItemProperty 'HKLM:\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\App Paths\\*' -ErrorAction SilentlyContinue | "
        f"Where-Object {{ $_.PSChildName -like '{app}*' }} | Select-Object -First 1; "
        f"if ($ap) {{ $p = $ap.'(default)' }} }} "
        f"if ($p) {{ $proc = Start-Process $p -PassThru; Start-Sleep -Seconds 3; "
        f"$w = Get-Process -Id $proc.Id -ErrorAction SilentlyContinue; "
        f"if ($w -and $w.MainWindowTitle) {{ 'OPENED pid=' + $proc.Id + ' title=[' + $w.MainWindowTitle + ']' }} "
        f"else {{ 'STARTED pid=' + $proc.Id + ' (window title not set yet)' }} }} "
        f"else {{ 'NOTFOUND: no app matching {app}' }}"
    )
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command", query],
            capture_output=True, text=True, timeout=30,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        ).stdout.strip()
    except Exception as exc:
        return f"Could not launch {name!r}: {exc}"
    return out or f"Could not launch {name!r} (no output)."


def close_app(name: str) -> str:
    """Close an application's windows by name (graceful, then force).

    Args:
        name: Process name, e.g. "notepad", "chrome", "winword" (no .exe).

    Returns:
        What was closed, or that nothing was running.
    """
    app = name.strip().lower().removesuffix(".exe")
    query = (
        f"$procs = Get-Process -Name '{app}' -ErrorAction SilentlyContinue | "
        "Where-Object { $_.MainWindowHandle -ne 0 }; "
        "if (-not $procs) { "
        f"  $any = Get-Process -Name '{app}' -ErrorAction SilentlyContinue; "
        "  if ($any) { 'BACKGROUND-ONLY: no visible window (all closed?)' } "
        "  else { 'NOTRUNNING: no process named " + app + "' } "
        "} else { "
        "  $procs | ForEach-Object { $_.CloseMainWindow() | Out-Null }; "
        "  Start-Sleep -Seconds 2; "
        f"  $left = Get-Process -Name '{app}' -ErrorAction SilentlyContinue; "
        "  if ($left) { "
        f"    $left | Stop-Process -Force; "
        f"    'FORCE-CLOSED: ' + (@($left).Count) + ' window(s) of " + app + "' "
        "  } else { "
        f"    'CLOSED: ' + (@($procs).Count) + ' window(s) of " + app + " gracefully' "
        "  } "
        "}"
    )
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command", query],
            capture_output=True, text=True, timeout=30,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        ).stdout.strip()
    except Exception as exc:
        return f"Could not close {name!r}: {exc}"
    return out or f"Could not close {name!r} (no output)."


def speak(text: str) -> str:
    """Speak text aloud through the speakers (Windows built-in voice).

    Use this to confirm to the user, out loud, what you did or what went
    wrong — e.g. after finishing desktop tasks.

    Args:
        text: What to say (plain text, kept reasonably short).

    Returns:
        Confirmation of the action.
    """
    if sys.platform != "win32":
        return "speak() is only supported on Windows."
    try:
        engine = _get_tts()
        engine.say(text)
        engine.runAndWait()
        return f"Spoke {len(text)} characters aloud."
    except Exception as exc:
        # Fallback: Windows SAPI via PowerShell (separate process).
        safe = text.replace("'", "''").replace('"', "")
        script = (
            "Add-Type -AssemblyName System.Speech; "
            "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
            f"$s.Speak('{safe}')"
        )
        try:
            subprocess.run(
                ["powershell", "-NoProfile", "-Command", script],
                capture_output=True, timeout=120,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            return f"Spoke via SAPI fallback ({exc})."
        except Exception as exc2:
            return f"Could not speak: {exc2}"


TOOLS: list[Callable[..., str]] = [
    now,
    calculator,
    web_fetch,
    web_search,
    read_file,
    write_file,
    append_file,
    list_directory,
    search_files,
    run_command,
    list_skills,
    get_skill,
    save_skill,
    screenshot,
    look_at_screen,
    mouse_move,
    mouse_click,
    mouse_drag,
    mouse_scroll,
    type_text,
    press_key,
    speak,
    open_app,
    close_app,
    remember,
    forget,
    search_notes,
    list_notes,
]


# ---------------------------------------------------------------------------
# Agent assembly
# ---------------------------------------------------------------------------


def build_system_prompt(settings: Settings) -> str:
    """Compose the agent's system prompt (identity, skills and guidance)."""
    catalog = SkillManager(settings.skills_dir).catalog_prompt()
    # The timestamp must NOT be in the system prompt for fast/voice mode: a
    # changing byte anywhere invalidates Ollama's prompt cache, forcing a full
    # re-evaluation of ~8k tokens per request (minutes!). The `now` tool
    # answers time questions anyway.
    now_str = "" if settings.fast else datetime.now().astimezone().isoformat(timespec="seconds")
    return textwrap.dedent(
        f"""
        You are {AGENT_NAME}, a capable personal AI assistant running fully
        locally — in the spirit of Jarvis from Iron Man: calm, competent,
        quietly witty. You help with coding, writing, research, system tasks
        and everyday questions — using your tools whenever they would make
        your answer better or more accurate.
        You address the user as "sir" (naturally, not in every sentence).
        After completing a task, say plainly whether it succeeded or failed —
        e.g. "Done, sir." / "I'm afraid that failed, sir — <reason>." — and
        keep it to one short closing line.

        ## Facts about this machine
        {f"- Current date & time (local): {now_str}" if now_str else "- For the current date/time, call the `now` tool."}
        - Working directory: {settings.workspace}
        - Your training data has a cutoff in the past. Whenever "now", "today",
          the date, or live/up-to-date data matters, call the `now` and/or web
          tools instead of guessing.

        ## Skills available (load with get_skill, create with save_skill)
        {catalog}

        ## Tool rules
        - Use a tool when it helps: calculator for math; read_file, write_file,
          append_file, list_directory and search_files for local files;
          run_command for shell operations; web_fetch and web_search (Brave or
          headless-Chrome/Edge, never DuckDuckGo) for the web;
          remember/search_notes for anything worth keeping long-term.
        - Prefer reading a file before editing it so your edits match reality.
        - Desktop control: look_at_screen SAVES a screenshot and returns what
          the vision model sees — use it to find window titles, buttons and
          coordinates before clicking. mouse_click/type_text/press_key act on
          whatever window has focus, so always LOOK first, then act. Screenshots
          are saved under .agent/screenshots/ and can be shown to the user.
        - To OPEN an application (notepad, word, chrome, brave, calculator...),
          always use the open_app tool — never run_command, whose timeout can
          kill the launched app and then reports a false success.
        - When a tool errors, adapt and try another approach instead of
          repeating the same call.
        - Never claim you did something (wrote a file, ran a command, searched
          the web) unless you actually called the tool that did it.

        ## Memory & history
        - You have access to the full prior conversation and to persistent
          notes (remember/search_notes). Use notes for facts the user wants to
          keep across conversations: preferences, decisions, references.
        - Prefer `remember` over asking the user to repeat themselves.

        ## Style
        - Be direct, concise and practical. Use short paragraphs or bullet
          lists. Show code with brief explanations, not essays.
        - If the request is ambiguous, state your assumption and proceed.
        - Lead with the answer, then support it.
        - Your replies are sometimes read aloud by text-to-speech, so keep
          sentences spoken-friendly: no markdown symbols, no giant lists
          when a sentence will do.
        """
    ).strip()


# Tool subset for --fast (voice) mode: every schema trimmed is thinking time
# saved on a local model — ~28 schemas cost ~2 min per turn on a laptop GPU.
FAST_TOOLS: list[Callable[..., str]] = [
    now,
    calculator,
    web_search,
    web_fetch,
    screenshot,
    look_at_screen,
    open_app,
    close_app,
    mouse_click,
    type_text,
    press_key,
    speak,
    remember,
]


def build_agent(settings: Settings) -> Agent:
    """Construct a pydantic-ai Agent wired to Ollama with all tools."""
    model = OllamaModel(
        settings.model,
        provider=OllamaProvider(base_url=settings.base_url),
        # Disable the model's verbose "thinking" mode: on local quantized
        # models it tends to ramble after tool results and can produce
        # responses that fail parsing. Text-only output is far more reliable.
        settings=OpenAIChatModelSettings(thinking=False),
    )
    return Agent(
        model,
        name=AGENT_NAME,
        system_prompt=build_system_prompt(settings),
        tools=FAST_TOOLS if settings.fast else TOOLS,
        # Local quantized models occasionally emit an unparseable turn after a
        # tool result — allow several regenerations instead of failing fast.
        retries={"tools": 2, "output": 4},
        defer_model_check=True,
    )


def _usage_summary(result: Any) -> str:
    try:
        u = result.usage
        parts = []
        for attr in ("requests", "tool_calls", "input_tokens", "output_tokens", "cache_read_tokens"):
            value = getattr(u, attr, None)
            if value is not None:
                parts.append(f"{attr}={value}")
        return " • " + ", ".join(parts) if parts else ""
    except Exception:
        return ""


def run_turn(
    agent: Agent,
    settings: Settings,
    prompt: str,
    history: list[ModelMessage],
    verbose: bool = True,
) -> Optional[str]:
    """Run one turn, print the answer, and persist the conversation."""
    start = time.monotonic()
    try:
        result = agent.run_sync(
            prompt,
            message_history=history or None,
            usage_limits=UsageLimits(request_limit=settings.request_limit),
        )
    except KeyboardInterrupt:
        print("\n(interrupted)")
        return None
    except Exception as exc:
        print(f"\n[error] {exc}")
        return None
    elapsed = time.monotonic() - start
    output = (result.output or "").strip()

    # Show which tools the agent physically used this turn (desktop actions
    # must be auditable — a false "done" is worse than an error).
    for msg in result.all_messages()[len(history):]:
        try:
            for part in getattr(msg, "parts", []):
                kind = getattr(part, "part_kind", "")
                if kind == "tool-call":
                    print(f"  [tool] {part.tool_name}({part.args})")
                elif kind == "tool-return":
                    ret = str(part.content).replace("\n", " ")[:160]
                    print(f"  [->] {ret}")
        except Exception:
            pass

    if output:
        print(f"\n{output}")
    else:
        print("\n(no text output)")

    new_history = list(result.all_messages())
    history.clear()
    history.extend(new_history)

    if settings.persist:
        to_save = history[-settings.max_history :] if len(history) > settings.max_history else history
        try:
            save_history(settings, to_save)
        except Exception as exc:
            print(f"\n[warning] could not save history: {exc}")

    if verbose:
        print(f"\n[ok] {elapsed:.1f}s{_usage_summary(result)}")
    return output


# ---------------------------------------------------------------------------
# Interactive REPL
# ---------------------------------------------------------------------------

HELP_TEXT = textwrap.dedent(
    """
    Commands
      /help                 show this help
      /new                  start a fresh conversation
      /tools                list registered tools
      /skills               list available skills
      /skill <name>         show one skill's instructions
      /notes                show all long-term notes
      /model <name>         switch to another Ollama model
      /history              show conversation stats
      /exit  (/quit)        leave the REPL
    """
)


def _print_tools() -> None:
    print(f"Registered tools ({len(TOOLS)}):")
    for fn in TOOLS:
        first = (fn.__doc__ or "no description").strip().splitlines()[0]
        print(f"  - {fn.__name__}: {first}")


def repl(agent: Agent, settings: Settings, history: list[ModelMessage]) -> int:
    print(
        f"\n{AGENT_NAME} — model: {settings.model}\n"
        "type /help for commands, /exit to quit.\n"
    )
    while True:
        try:
            line = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not line:
            continue
        low = line.lower()
        if low in {"/exit", "/quit", "/q"}:
            break
        if low == "/help":
            print(HELP_TEXT)
        elif low == "/new":
            if settings.persist:
                archive_history(settings)
            history.clear()
            print("New conversation started.")
        elif low == "/tools":
            _print_tools()
        elif low == "/skills":
            print(list_skills())
        elif low.startswith("/skill "):
            print(get_skill(line.split(maxsplit=1)[1]))
        elif low == "/notes":
            print(list_notes())
        elif low.startswith("/model "):
            name = line.split(maxsplit=1)[1].strip()
            if not name:
                print("Usage: /model <model-name>")
                continue
            settings.model = name
            try:
                agent = build_agent(settings)
            except Exception as exc:
                print(f"[error] could not switch model: {exc}")
                continue
            print(f"Switched to model {name!r}.")
        elif low == "/history":
            print(f"Conversation has {len(history)} messages.")
        elif low.startswith("/"):
            print(f"Unknown command {line!r}. Try /help")
        else:
            run_turn(agent, settings, line, history)
    print("bye.")
    return 0
# ---------------------------------------------------------------------------
# Voice mode — listen (speech-to-text), think, act, then speak the answer
# ---------------------------------------------------------------------------

_WAKE_WORDS = {"jarvis", "hey jarvis", "agent"}


def _speech_text(text: str) -> str:
    """Strip markdown-ish symbols so TTS sounds natural."""
    return re.sub(r"[*_`#>|]", "", text)


def _listen_once(recognizer: Any, microphone: Any) -> Optional[str]:
    with microphone as source:
        recognizer.adjust_for_ambient_noise(source, duration=0.4)
        print("listening… (speak now)", flush=True)
        try:
            audio = recognizer.listen(source, timeout=8, phrase_time_limit=30)
        except Exception:
            return None
    try:
        return recognizer.recognize_google(audio).strip()
    except Exception:
        return None


def voice_repl(agent: Agent, settings: Settings, history: list[ModelMessage]) -> int:
    """Hands-free loop: speak a request, the agent answers aloud."""
    try:
        import speech_recognition as sr
    except ImportError:
        print("Voice input needs two packages:\n"
              "    pip install SpeechRecognition pyaudio\n"
              "(then run: python agent.py --voice)")
        return 1
    recognizer = sr.Recognizer()
    try:
        microphone = sr.Microphone()
    except Exception as exc:
        print(f"[error] microphone unavailable: {exc}")
        return 1

    print(
        f"\n{AGENT_NAME} (voice) — model: {settings.model}\n"
        "Speak your request. Say 'goodbye' to quit.\n"
    )
    speak("Online and listening, sir.")
    while True:
        heard = _listen_once(recognizer, microphone)
        if not heard:
            print("(didn't catch that — try again)")
            continue
        print(f"you (voice)> {heard}")
        if heard.lower().strip(" .!") in {"goodbye", "good bye", "exit", "quit", "stop"}:
            speak("Goodbye, sir.")
            break
        # Ack immediately — the model can take minutes, silence feels broken.
        speak("Right away, sir.")
        answer = run_turn(agent, settings, heard, history)
        if answer:
            speak(_speech_text(answer))
        else:
            speak("I'm afraid something went wrong there, sir.")
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    global _current_settings

    parser = argparse.ArgumentParser(
        prog="agent.py",
        description=f"{AGENT_NAME} — local tools-and-skills agent on Ollama.",
    )
    parser.add_argument("prompt", nargs="?", help="one-shot prompt (omit to enter the REPL)")
    parser.add_argument("--model", default=os.environ.get("OLLAMA_MODEL", DEFAULT_MODEL),
                        help="Ollama model name")
    parser.add_argument("--base-url", default=os.environ.get("OLLAMA_BASE_URL", DEFAULT_BASE_URL),
                        help="Ollama OpenAI-compatible base URL")
    parser.add_argument("--search-backend", choices=["auto", "brave", "chrome"],
                        default=os.environ.get("SEARCH_BACKEND", "auto"),
                        help="web search backend: 'brave' (needs BRAVE_API_KEY), "
                             "'chrome' (Google/Bing via installed Chrome/Edge), or 'auto'")
    parser.add_argument("--workspace", type=Path, default=PROJECT_DIR,
                        help="working directory for file/command tools")
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR,
                        help="where history and notes are stored")
    parser.add_argument("--skills-dir", type=Path, default=DEFAULT_SKILLS_DIR,
                        help="where skill files live")
    parser.add_argument("--new", action="store_true",
                        help="start a fresh conversation (archive old history)")
    parser.add_argument("--no-history", action="store_true",
                        help="do not load or save conversation history")
    parser.add_argument("--request-limit", type=int, default=100,
                        help="max model requests per turn")
    parser.add_argument("--max-history", type=int, default=40,
                        help="most recent messages kept in saved history")
    parser.add_argument("--list-tools", action="store_true",
                        help="print registered tools and exit")
    parser.add_argument("--list-skills", action="store_true",
                        help="print available skills and exit")
    parser.add_argument("--print-system-prompt", action="store_true",
                        help="print the generated system prompt and exit")
    parser.add_argument("--voice", action="store_true",
                        help="hands-free mode: listen via microphone, answer aloud "
                             "(needs: pip install SpeechRecognition pyaudio)")
    parser.add_argument("--fast", action="store_true",
                        help="essential tools only — much faster turns (implied by --voice)")
    args = parser.parse_args(argv)

    settings = Settings(
        model=args.model,
        base_url=args.base_url,
        workspace=args.workspace,
        data_dir=args.data_dir,
        skills_dir=args.skills_dir,
        request_limit=args.request_limit,
        max_history=args.max_history,
        persist=not args.no_history,
        search_backend=args.search_backend,
        fast=args.fast or args.voice,
    )
    _current_settings = settings

    if args.list_tools:
        _print_tools()
        return 0
    if args.list_skills:
        print(list_skills())
        print(f"\n(skill files live in: {settings.skills_dir})")
        return 0
    if args.print_system_prompt:
        print(build_system_prompt(settings))
        return 0

    if args.new and settings.persist and settings.history_file.exists():
        archive_history(settings)

    history = load_history(settings) if settings.persist else []

    try:
        agent = build_agent(settings)
    except Exception as exc:
        print(f"[error] could not initialise agent: {exc}")
        print("Is Ollama running? See --help.")
        return 1

    if args.prompt:
        if history:
            print(f"(resuming conversation from {settings.history_file.name} — {len(history)} messages)")
        output = run_turn(agent, settings, args.prompt, history)
        if args.voice and output:
            speak(_speech_text(output))
        return 0

    if args.voice:
        return voice_repl(agent, settings, history)

    if history:
        print(f"(resuming conversation — {len(history)} messages in context)")
    return repl(agent, settings, history)


if __name__ == "__main__":
    sys.exit(main())