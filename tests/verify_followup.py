"""verify_followup.py — confirm the file on disk is untouched + get the window title properly."""

import glob
import os
import subprocess
import time

# --- 1) was the stray keystroke sentence saved into Steam_Passes.txt? -------
print("=== 1) checking Steam_Passes.txt on disk ===")
bases = [os.path.expanduser(p) for p in ("~\\Desktop", "~\\Documents", "~\\Downloads")]
hits = [h for b in bases for h in glob.glob(os.path.join(b, "**", "Steam_Passes.txt"), recursive=True)]
if not hits:
    print("not found under Desktop/Documents/Downloads (tell me the path and I'll check)")
else:
    for h in hits:
        print("found:", h)
        with open(h, encoding="utf-8", errors="replace") as f:
            content = f.read()
        print("last 3 lines on disk:")
        for ln in content.splitlines()[-3:]:
            print("   |", ln)
        leaked = "typed by simulated keystrokes" in content
        print("stray sentence present in file:", leaked)

# --- 2) programmatic notepad, with a retry loop for the window title --------
print("\n=== 2) programmatic notepad, title via retry ===")
t0 = time.perf_counter()
with open("prog_test.txt", "w", encoding="utf-8") as f:
    f.write("programmatic test line")
p = subprocess.Popen(["notepad", "prog_test.txt"])

title = ""
while time.perf_counter() - t0 < 3:
    r = subprocess.run(
        ["powershell", "-NoProfile", "-Command", f"(Get-Process -Id {p.pid}).MainWindowTitle"],
        capture_output=True, text=True,
    ).stdout.strip()
    if r:
        title = r
        break
    time.sleep(0.1)

print(f"window title           : {title!r}")
print(f"elapsed to get title   : {(time.perf_counter() - t0) * 1000:.0f} ms")
subprocess.run(["taskkill", "/PID", str(p.pid), "/F"], capture_output=True)
if os.path.exists("prog_test.txt"):
    os.remove("prog_test.txt")
print("cleaned up; done")
