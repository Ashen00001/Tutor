"""Voice path test: tutor server + mock ElevenLabs + headless Chromium playing the audio.

python tests/voice_test.py
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
PY = sys.executable


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0)); return s.getsockname()[1]


def wait(url: str) -> None:
    for _ in range(60):
        try:
            urllib.request.urlopen(url, timeout=1); return
        except Exception:
            time.sleep(0.3)
    raise RuntimeError(url)


def check(c: bool, msg: str) -> None:
    print(("  PASS " if c else "  FAIL ") + msg)
    if not c:
        raise SystemExit(1)


tmp = Path(tempfile.mkdtemp(prefix="jpt-voice-"))
procs = []
try:
    mport, port = free_port(), free_port()
    procs.append(subprocess.Popen([PY, str(ROOT / "tests" / "mock_elevenlabs.py"), str(mport)]))
    wait(f"http://127.0.0.1:{mport}/calls")
    env = {**os.environ, "JP_TUTOR_DATA": str(tmp), "JP_TUTOR_SECRET": "s3cret", "PORT": str(port), "HOST": "127.0.0.1",
           "ELEVENLABS_API_KEY": "test-key", "ELEVENLABS_BASE_URL": f"http://127.0.0.1:{mport}", "PYTHONPATH": str(ROOT / "src")}
    log = open(tmp / "server.log", "w")
    procs.append(subprocess.Popen([PY, "-m", "anki_jp_tutor"], env=env, stdout=log, stderr=log))
    wait(f"http://127.0.0.1:{port}/health")
    api = f"http://127.0.0.1:{port}/api/s3cret"

    print("== server voice routes")
    cfg = json.load(urllib.request.urlopen(api + "/config"))
    check(cfg["tts"] == {"provider": "elevenlabs", "model": "eleven_v4_turbo"}, "config reports ElevenLabs v4 Turbo")
    v = json.load(urllib.request.urlopen(api + "/voices"))
    check(v["default"] == "aaaaaaaaaaaaaaaaaaaa" and len(v["voices"]) == 2, "voices listed, Jessica picked as default")
    q = urllib.parse.urlencode({"text": "みず"})
    r1 = urllib.request.urlopen(f"{api}/tts?{q}"); b1 = r1.read()
    r2 = urllib.request.urlopen(f"{api}/tts?{q}"); b2 = r2.read()
    check(r1.headers["X-Cache"] == "miss" and r2.headers["X-Cache"] == "hit" and b1 == b2, "second request for the same line is served from cache")
    calls = json.load(urllib.request.urlopen(f"http://127.0.0.1:{mport}/calls"))
    check(len(calls) == 1 and calls[0]["model"] == "eleven_v4_turbo", "only one paid call, with model eleven_v4_turbo")
    try:
        urllib.request.urlopen(f"{api}/tts?" + urllib.parse.urlencode({"text": "x", "voice": "../../etc"}))
        check(False, "bad voice id rejected")
    except urllib.error.HTTPError as e:
        check(e.code == 502, "bad voice id rejected")

    print("== app plays ElevenLabs audio")
    with sync_playwright() as pw:
        b = pw.chromium.launch(args=["--autoplay-policy=no-user-gesture-required", "--use-angle=swiftshader", "--enable-unsafe-swiftshader"])
        page = b.new_page(viewport={"width": 390, "height": 844})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(f"http://127.0.0.1:{port}/app/s3cret/")
        page.click("#goBtn")
        page.wait_for_selector(".tiles .tile")
        check(page.evaluate("!!TTS.cloud && !!TTS.ctx"), "app switched to the ElevenLabs voice")
        page.wait_for_function("TTS.src !== null", timeout=8000)
        lvl = page.evaluate("new Promise(r => setTimeout(() => r(orb.meter ? TTS.level() : null), 250))")
        check(lvl is not None and lvl > 0.01, f"orb is driven by the real audio level ({lvl:.3f})")
        page.wait_for_function("TTS.src === null", timeout=15000)
        page.click("#goQuiz")
        page.wait_for_selector(".prompt")
        page.wait_for_function("TTS.src === null", timeout=10000)
        page.click("#kbBtn"); page.fill("#input", "nope"); page.press("#input", "Enter")
        page.wait_for_selector(".pill.wrong")
        page.wait_for_function("document.querySelectorAll('#line .w.on').length > 0", timeout=8000)
        page.wait_for_function("TTS.src === null && document.querySelectorAll('#line .w:not(.on)').length === 0", timeout=15000)
        check(True, "feedback line spoken and every word lit by the end")
        page.click("#menuBtn")
        page.wait_for_function("document.querySelectorAll('#voiceSel option').length === 2")
        page.select_option("#voiceSel", "bbbbbbbbbbbbbbbbbbbb")
        check(page.evaluate("TTS.cloud.voice") == "bbbbbbbbbbbbbbbbbbbb", "voice picker switches voice")
        page.click("#voiceTry")
        page.wait_for_function("TTS.src !== null", timeout=8000)
        b.close()
    calls = json.load(urllib.request.urlopen(f"http://127.0.0.1:{mport}/calls"))
    check(any(c["voice"] == "bbbbbbbbbbbbbbbbbbbb" for c in calls), "picked voice is used for synthesis")
    check(not errors, f"no page errors {errors[:2]}")
    print(f"  ({len(calls)} synth calls total)")
    print("VOICE CHECKS PASSED")
finally:
    for p in procs:
        p.terminate()
    shutil.rmtree(tmp, ignore_errors=True)
