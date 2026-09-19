"""test_kokoro.py — benchmark Kokoro-82M backends on the RTX 3050.

Tries in order: DML fp16, DML fp32, CPU fp32 (forced session swap).
Plays the winner so you can hear it.
"""

import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import numpy as np  # noqa: E402
import onnxruntime as ort  # noqa: E402
import soundfile as sf  # noqa: E402
from kokoro_onnx import Kokoro  # noqa: E402

print("providers:", ort.get_available_providers())

root = Path(__file__).resolve().parents[1] / ".agent" / "tts"
voices = root / "voices-v1.0.bin"
TEXT = "Good evening, sir. All systems are online. The new voice engine is ready for duty."
RESULTS = []


def bench(label: str, model_path: Path, force_cpu: bool = False) -> None:
    try:
        t0 = time.perf_counter()
        kokoro = Kokoro(str(model_path), str(voices))
        if force_cpu:
            # kokoro-onnx does not expose providers; swap the session ourselves
            kokoro.sess = ort.InferenceSession(
                str(model_path), providers=["CPUExecutionProvider"]
            )
        load = time.perf_counter() - t0

        t1 = time.perf_counter()
        audio, sr = kokoro.create(TEXT, voice="am_michael", speed=1.0, lang="en-us")
        gen = time.perf_counter() - t1
        dur = len(audio) / sr
        print(f"[{label}] load {load:.1f}s | generate {gen:.2f}s | audio {dur:.1f}s "
              f"-> {dur / gen:.1f}x realtime")
        RESULTS.append((label, kokoro, gen, dur))
    except Exception as exc:
        msg = str(exc).replace("\n", " ")[:120]
        print(f"[{label}] FAILED: {msg}")


fp16 = root / "kokoro-v1.0.fp16.onnx"
fp32 = root / "kokoro-v1.0.onnx"

if fp16.exists():
    bench("DML fp16", fp16)
bench("DML fp32", fp32)
bench("CPU fp32", fp32, force_cpu=True)

if RESULTS:
    RESULTS.sort(key=lambda r: r[2])
    label, kokoro, gen, dur = RESULTS[0]
    print(f"\nWINNER: {label} ({gen:.2f}s)")
    out = root / "kokoro_test.wav"
    sf.write(out, kokoro.create(TEXT, voice="am_michael", speed=1.0, lang="en-us")[0],
             24000)
    import winsound
    print("playing winner...")
    winsound.PlaySound(str(out), winsound.SND_FILENAME)
    print("done")
