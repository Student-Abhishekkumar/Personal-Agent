"""compare_prog.py — the programmatic alternative to the Win-key path."""

import os
import subprocess
import time

t0 = time.perf_counter()

# 1) put the text where it belongs -- the file system, not a keyboard buffer
with open("prog_test.txt", "w", encoding="utf-8") as f:
    f.write("Hello from the agent - written via the file API, no keystrokes involved.")

# 2) open it in Notepad, keeping the PID so cleanup is surgical
p = subprocess.Popen(["notepad", "prog_test.txt"])
spawn_ms = (time.perf_counter() - t0) * 1000

# 3) settle time ONLY so the window exists long enough to prove it opened
time.sleep(0.7)

title = subprocess.run(
    ["powershell", "-NoProfile", "-Command", f"(Get-Process -Id {p.pid}).MainWindowTitle"],
    capture_output=True, text=True,
).stdout.strip()

subprocess.run(["taskkill", "/PID", str(p.pid), "/F"],
               capture_output=True)
os.remove("prog_test.txt")

print(f"title of spawned window : {title!r}")
print(f"agent work (write+spawn): {spawn_ms:.1f} ms")
print(f"total incl. settle time : {(time.perf_counter() - t0) * 1000:.0f} ms")
print("done")
