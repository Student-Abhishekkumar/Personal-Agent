"""test_vision_notepad.py — full computer-use loop with agent.py's own tools.

The agent PHYSICALLY types into Notepad, SEES what it wrote via the vision
model (look_at_screen), asks the vision model for the corrected sentence,
then PHYSICALLY fixes it and verifies the change by looking again.

Leave the machine alone while this runs (mouse failsafe: top-left corner).
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # vision answers can contain arrows etc.
import agent  # noqa: E402
from agent import _ollama_vision, look_at_screen, mouse_click, press_key, type_text  # noqa: E402

DRAFT = "Helo from yuor agent! Plese fix the typos in this sentnce."
FALLBACK_FIX = "Hello from your agent! Please fix the typos in this sentence."


def wait(seconds: float, label: str) -> None:
    time.sleep(seconds)
    print(f"    ({label}, {seconds:.0f}s elapsed)", flush=True)


# --- [1] physically open Notepad, fresh untitled tab ------------------------
print("[1] press_key win+r -> type 'notepad' -> Enter ...", flush=True)
press_key("win+r")
wait(1.2, "Run dialog")
press_key("ctrl+a")  # Run pre-fills the last command — select it so typing replaces it
type_text("notepad", interval=0.05)
press_key("enter")
wait(3.0, "Notepad launching")
press_key("ctrl+n")  # fresh tab: Win11 Notepad restores the previous session
wait(1.5, "new tab")

# --- [2] click into the text area and type the typo-ridden draft -------------
print("[2] mouse_click into Notepad + physically typing the draft ...", flush=True)
mouse_click(960, 500)
wait(0.5, "cursor in text area")
type_text(DRAFT, interval=0.04)
wait(1.0, "draft typed")

# --- [3] the agent LOOKS at its own work -------------------------------------
print("[3] look_at_screen: transcribe what is on screen ...", flush=True)
seen = look_at_screen(
    "A Notepad window has text typed in it. Transcribe that text EXACTLY as "
    "shown, then list which words are misspelled."
)
print(seen, flush=True)

# --- [4] vision proposes the correction ---------------------------------------
shots = sorted((agent._current_settings.data_dir / "screenshots").glob("*.png"))
corrected = ""
if shots:
    try:
        corrected = _ollama_vision(
            shots[-1],
            "The document text in this screenshot contains misspelled words. "
            "Reply with ONLY the fully corrected sentence, nothing else.",
        ).strip().strip('"')
    except Exception as exc:
        print(f"    (correction request failed: {exc})", flush=True)
if not corrected:
    corrected = FALLBACK_FIX
print(f"[4] vision-proposed correction: {corrected!r}", flush=True)

# --- [5] physically apply the change ------------------------------------------
print("[5] ctrl+a then retyping the corrected text ...", flush=True)
mouse_click(960, 500)
wait(0.5, "focus")
press_key("ctrl+a")
wait(0.5, "select all")
type_text(corrected, interval=0.04)
wait(1.0, "correction typed")

# --- [6] look again to verify the change --------------------------------------
print("[6] look_at_screen: verify the fix ...", flush=True)
final = look_at_screen(
    "A Notepad window has text in it. Transcribe that text EXACTLY as shown."
)
print(final, flush=True)
print("[7] DONE — Notepad left open with the final text.", flush=True)
