"""test_win_key.py — live GUI-automation test.

Pipeline: Win+R -> type "notepad" -> Enter -> type a sentence ->
screenshot -> kill only the Notepad instance this script spawned.

Run it and DON'T touch the mouse/keyboard for ~8 seconds.
Slamming the mouse into the top-left corner aborts instantly (pyautogui failsafe).
"""

import subprocess
import sys
import time

import pyautogui

pyautogui.FAILSAFE = True
pyautogui.PAUSE = 0.3  # global gap between every simulated key


def ps(cmd: str) -> str:
    return subprocess.run(
        ["powershell", "-NoProfile", "-Command", cmd],
        capture_output=True, text=True,
    ).stdout.strip()


def notepad_titles() -> list[str]:
    out = ps(
        "(Get-Process -Name notepad -ErrorAction SilentlyContinue) "
        "| ForEach-Object { '{0} pid={1}' -f $_.MainWindowTitle, $_.Id }"
    )
    return [ln for ln in out.splitlines() if ln.strip()]


t0 = time.perf_counter()
pids_before = set(ps("(Get-Process -Name notepad -ErrorAction SilentlyContinue).Id").split())
print(f"[1] notepad running before: {sorted(pids_before) or '(none)'}")

print("[2] sending Win+R ...")
pyautogui.hotkey("win", "r")
time.sleep(1.2)  # hope the Run dialog is up by now -- no way to actually know

print("[3] typing 'notepad' + Enter (into whatever has focus!) ...")
pyautogui.typewrite("notepad", interval=0.06)
pyautogui.press("enter")
time.sleep(2.0)

wins = notepad_titles()
print(f"[4] notepad windows after: {wins or '(none)'}")
if not wins:
    print("FAILED: no Notepad window appeared (focus/timing/locale).")
    sys.exit(1)

print("[5] typing a sentence into whatever now has focus ...")
pyautogui.typewrite(
    "Hello from the agent - this line was typed by simulated keystrokes.",
    interval=0.02,
)
time.sleep(0.8)

print("[6] screenshot of the desktop ...")
shot = pyautogui.screenshot()
shot.save("win_key_test.png")
print(f"    saved win_key_test.png size={shot.size}")

spawned = set(ps("(Get-Process -Name notepad -ErrorAction SilentlyContinue).Id").split()) - pids_before
print(f"[7] killing only the notepad PID(s) this test spawned: {sorted(spawned)}")
for pid in spawned:
    ps(f"Stop-Process -Id {pid} -Force")
time.sleep(0.6)
leftover = notepad_titles()
print(f"[8] notepad windows after cleanup: {leftover or '(none)'}")

print(f"[9] total time for the GUI path: {time.perf_counter() - t0:.2f} s")
print("done")
