"""test_vision.py — live test of look_at_screen (screenshot -> vision model)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import agent  # noqa: E402

question = "What application windows are visible on this screen? Name their titles and one visible button in each."
print("asking the vision model ...", flush=True)
print(agent.look_at_screen(question))
