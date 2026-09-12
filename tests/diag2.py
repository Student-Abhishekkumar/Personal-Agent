"""diag2.py — who owns the Notepad window + is the stray sentence in TabState?"""

import os
import subprocess
import time
from pathlib import Path

# --- 1) spawn notepad, then inspect which process actually owns the window --
with open("prog_test.txt", "w", encoding="utf-8") as f:
    f.write("diag line")
p = subprocess.Popen(["notepad", "prog_test.txt"])
time.sleep(2.5)

q = (
    "Get-Process | Where-Object { $_.MainWindowTitle -like '*prog_test*' } "
    "| ForEach-Object { 'owner: {0} pid={1} hwnd={2} title={3}' -f "
    "$_.ProcessName, $_.Id, $_.MainWindowHandle, $_.MainWindowTitle }; "
    "Get-Process -Name notepad -ErrorAction SilentlyContinue "
    "| ForEach-Object { 'notepad-proc: pid={0} hwnd={1} title=[{2}]' -f "
    "$_.Id, $_.MainWindowHandle, $_.MainWindowTitle }"
)
print(subprocess.run(["powershell", "-NoProfile", "-Command", q],
                     capture_output=True, text=True).stdout.strip() or "(no windows found)")
subprocess.run(["taskkill", "/PID", str(p.pid), "/F"], capture_output=True)
os.remove("prog_test.txt")

# --- 2) is the stray sentence inside Notepad's session-restore cache? -------
print("\n=== 2) Notepad TabState cache ===")
tab = Path(os.environ["LOCALAPPDATA"]) / "Packages" / "Microsoft.WindowsNotepad_8wekyb3d8bbwe" / "LocalState" / "TabState"
needle = b"simulated keystrokes"
if not tab.exists():
    print("no TabState dir at", tab)
else:
    found = False
    for bin_file in sorted(tab.glob("*.bin"), key=lambda f: f.stat().st_mtime, reverse=True):
        data = bin_file.read_bytes()
        if needle in data:
            found = True
            print(f"STRAY TEXT STILL CACHED: {bin_file.name} (mtime {bin_file.stat().st_mtime})")
    if not found:
        print("stray sentence is NOT in the cache - it died with the killed process, disk file unaffected")
    newest = max(tab.glob("*.bin"), key=lambda f: f.stat().st_mtime, default=None)
    if newest:
        import datetime
        ts = datetime.datetime.fromtimestamp(newest.stat().st_mtime)
        print(f"newest cache entry: {newest.name} at {ts:%Y-%m-%d %H:%M:%S}")
