"""test_open_apps.py — open Word (programmatic) and Brave (Win-key, with fallback).

Word  : Start-Process (App Paths) -> verified window title, LEFT OPEN.
Brave : Win+R -> type "brave" -> Enter (GUI path). If Run cannot resolve the
        per-user install, falls back to Start-Process. LEFT OPEN.
"""

import subprocess
import time

import pyautogui

pyautogui.FAILSAFE = True  # slam mouse to top-left corner to abort
pyautogui.PAUSE = 0.3


def ps(cmd: str) -> str:
    return subprocess.run(
        ["powershell", "-NoProfile", "-Command", cmd],
        capture_output=True, text=True,
    ).stdout.strip()


def windows() -> list[str]:
    out = ps(
        "Get-Process -Name WINWORD,brave -ErrorAction SilentlyContinue "
        "| ForEach-Object { '{0} pid={1} hwnd={2} title=[{3}]' -f "
        "$_.ProcessName, $_.Id, $_.MainWindowHandle, $_.MainWindowTitle }"
    )
    return [ln for ln in out.splitlines() if ln.strip()]


def wait_for(proc: str, seconds: float) -> list[str]:
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < seconds:
        rows = [ln for ln in windows() if proc.lower() in ln.lower() and "hwnd=0 " not in ln]
        if rows:
            return rows
        time.sleep(0.5)
    return []


print("[1] word/brave windows before:")
for ln in (windows() or ["(none)"]):
    print("   ", ln)

# --- Word: programmatic (App Paths resolution via Start-Process) -----------
print("[2] opening Word via Start-Process (programmatic) ...")
word_pid = ps("$p = Start-Process winword -PassThru; $p.Id").strip()
rows = wait_for("WINWORD", 25)
print(f"    spawned pid={word_pid}; window: {rows or '(none yet - Word can be slow on cold start)'}")

# --- Brave: the Win-key path ------------------------------------------------
print("[3] GUI path: Win+R -> 'brave' -> Enter (don't touch the keyboard) ...")
pyautogui.hotkey("win", "r")
time.sleep(1.2)
pyautogui.typewrite("brave", interval=0.06)
pyautogui.press("enter")
rows = wait_for("brave", 15)
if rows:
    print(f"    Win-key path WORKED: {rows}")
else:
    print("    Win-key path produced no window -> fallback: Start-Process (per-user exe) ...")
    ps("Start-Process \"$env:LOCALAPPDATA\\BraveSoftware\\Brave-Browser\\Application\\brave.exe\"")
    rows = wait_for("brave", 15)
    print(f"    fallback window: {rows or '(none)'}")

print("[4] final state (both apps left open for you to see):")
for ln in (windows() or ["(none)"]):
    print("   ", ln)
print("done")
