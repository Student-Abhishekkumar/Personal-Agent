"""test_chrome_search.py — exercise the agent's real chrome search path (no LLM).

Forces search_backend="chrome" so _search_brave is skipped, then calls the
same web_search() the agent tool uses: _search_chrome -> _chrome_dom ->
headless Chrome/Edge --dump-dom rendering of a live Google/Bing SERP.
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import agent  # noqa: E402

agent._current_settings.search_backend = "chrome"

QUERY = "latest stable python version"
print(f"query: {QUERY!r}  (backend forced to: chrome)")
t0 = time.perf_counter()
result = agent.web_search(QUERY, max_results=5)
dt = time.perf_counter() - t0

print(result[:2000])
print(f"\n[chrome search path took: {dt:.1f} s]")
