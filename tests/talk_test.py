"""Conversation mode test: tutor server + mock Claude (streaming, tools) + mock ElevenLabs (TTS + STT)
+ headless Chromium with a fake microphone.

python tests/talk_test.py
"""

from __future__ import annotations

import json
import math
import os
import random
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import wave
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


def post(url: str, body: dict) -> list[dict]:
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"content-type": "application/json"}, method="POST")
    return [json.loads(l) for l in urllib.request.urlopen(req).read().decode().splitlines() if l.strip()]


def speech_wav(path: Path) -> None:
    """1.2 s of voice-ish sound then 2.5 s of silence (the fake mic loops this)."""
    rate = 16000
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate)
        frames = []
        for i in range(int(rate * 1.2)):
            env = 0.5 + 0.5 * math.sin(i / 400)
            frames.append(struct.pack("<h", int(12000 * env * (math.sin(2 * math.pi * 180 * i / rate) + 0.3 * random.uniform(-1, 1)))))
        frames += [struct.pack("<h", 0)] * int(rate * 2.5)
        w.writeframes(b"".join(frames))


tmp = Path(tempfile.mkdtemp(prefix="jpt-talk-"))
procs = []
try:
    eport, aport, port = free_port(), free_port(), free_port()
    procs.append(subprocess.Popen([PY, str(ROOT / "tests" / "mock_elevenlabs.py"), str(eport)]))
    procs.append(subprocess.Popen([PY, str(ROOT / "tests" / "mock_anthropic.py"), str(aport)]))
    wait(f"http://127.0.0.1:{eport}/calls"); wait(f"http://127.0.0.1:{aport}/")
    env = {**os.environ, "JP_TUTOR_DATA": str(tmp / "data"), "JP_TUTOR_SECRET": "s3cret", "PORT": str(port), "HOST": "127.0.0.1",
           "ELEVENLABS_API_KEY": "test-key", "ELEVENLABS_BASE_URL": f"http://127.0.0.1:{eport}",
           "ANTHROPIC_API_KEY": "test-key", "JP_TUTOR_ANTHROPIC_BASE_URL": f"http://127.0.0.1:{aport}", "PYTHONPATH": str(ROOT / "src")}
    log = open(tmp / "server.log", "w")
    procs.append(subprocess.Popen([PY, "-m", "anki_jp_tutor"], env=env, stdout=log, stderr=log))
    wait(f"http://127.0.0.1:{port}/health")
    api = f"http://127.0.0.1:{port}/api/s3cret"

    print("== conversation API")
    cfg = json.load(urllib.request.urlopen(api + "/config"))
    check(cfg["chat"] and cfg["stt"] and "konbini" in cfg["scenarios"], "config: conversation + speech-to-text on")
    post(api + "/start", {})  # unlock batch 1 (水, はい, 食べる)
    ev = post(api + "/chat", {"messages": []})
    text = "".join(e["text"] for e in ev if e["t"] == "delta")
    check(ev[-1]["t"] == "done" and "What's up?" in text, "opening line streams in pieces")
    before = json.load(urllib.request.urlopen(api + "/progress"))["today"]
    ev = post(api + "/chat", {"messages": [{"role": "assistant", "content": "Yo!"}, {"role": "user", "content": "I want water"}], "scenario": "konbini"})
    kinds = [e["t"] for e in ev]
    check("reviewed" in kinds and next(e for e in ev if e["t"] == "reviewed")["word"] == "水", "using 水 correctly is recorded")
    check(json.load(urllib.request.urlopen(api + "/progress"))["today"] == before + 1, "…and counts as an Anki review")
    check("みず[mizu]" in ev[-1]["text"], "reply continues after the tool call")
    calls = json.load(urllib.request.urlopen(f"http://127.0.0.1:{aport}/"))
    check(calls[-1]["model"] == "claude-haiku-4-5-20251001", "uses Claude Haiku by default")
    check("水 = water" in calls[-1]["system"] and "convenience-store" in calls[-1]["system"], "system prompt has the deck and the scenario")
    check(calls[-1]["messages"][0]["role"] == "user", "history starts with a user turn")
    ev = post(api + "/chat", {"messages": [{"role": "user", "content": "save ganbaru pls"}]})
    check(any(e["t"] == "saved" and "頑張る" in e["words"] for e in ev), "words can be saved from conversation")
    req = urllib.request.Request(api + "/stt", data=b"\x1a" * 5000, headers={"content-type": "audio/webm"}, method="POST")
    check(json.load(urllib.request.urlopen(req))["text"] == "I want some water", "speech-to-text proxy works")

    print("== talking in the app with a (fake) microphone")
    wav = tmp / "speech.wav"; speech_wav(wav)
    with sync_playwright() as pw:
        b = pw.chromium.launch(args=["--autoplay-policy=no-user-gesture-required", "--use-fake-device-for-media-stream",
                                     "--use-fake-ui-for-media-stream", f"--use-file-for-fake-audio-capture={wav}",
                                     "--use-angle=swiftshader", "--enable-unsafe-swiftshader"])
        ctx = b.new_context(viewport={"width": 390, "height": 844}, permissions=["microphone"])
        page = ctx.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(f"http://127.0.0.1:{port}/app/s3cret/")
        page.click("#talkBtn")
        page.wait_for_selector(".bot .sent")
        check("What's up?" in page.inner_text("#convo"), "tutor opens the conversation")
        page.wait_for_function("S.talk.listening === true", timeout=15000)
        check(True, "listens automatically after speaking")
        page.wait_for_selector(".you", timeout=15000)
        check("water" in page.inner_text(".you"), "your speech shows up as text")
        page.wait_for_selector(".tagchip.ok", timeout=10000)
        check("水" in page.inner_text(".tagchip.ok"), "review chip appears")
        page.wait_for_function("document.querySelectorAll('.bot')[1]?.querySelectorAll('.sent.done').length >= 3", timeout=15000)
        check(page.locator(".bot .ro").count() >= 1, "romaji shows small next to new words")
        page.screenshot(path=str(ROOT / "tests" / "talk.png"))
        page.fill("#input", "save ganbaru pls") if page.is_visible("#input") else (page.click("#kbBtn"), page.fill("#input", "save ganbaru pls"))
        page.press("#input", "Enter")
        page.wait_for_function("[...document.querySelectorAll('.bot .sent')].some(x => x.textContent.includes('Saving that'))", timeout=10000)
        page.wait_for_timeout(600)
        yous = page.eval_on_selector_all(".you", "els => els.map(e => e.textContent)")
        check(yous[-1] == "save ganbaru pls" and sum("water" in y for y in yous) == 1, "typing works mid-listen, and the half-heard recording is dropped")
        page.click(".chipbtn[data-k='ramen']")
        page.wait_for_function("S.talk.history.length <= 2 && document.querySelector('.chipbtn.on').dataset.k === 'ramen'")
        check(True, "switching topic starts a fresh conversation")
        page.click("#endTalkBtn")
        page.wait_for_selector(".hero-copy h1")
        check(page.evaluate("Mic.stream === null"), "leaving talk mode releases the mic")
        b.close()
    stt_calls = [c for c in json.load(urllib.request.urlopen(f"http://127.0.0.1:{eport}/calls")) if c.get("stt")]
    check(stt_calls and stt_calls[-1]["has_model"] and stt_calls[-1]["bytes"] > 1000, f"recorded audio sent to Scribe v2 ({stt_calls[-1]['bytes'] if stt_calls else 0} bytes)")
    check(not errors, f"no page errors {errors[:2]}")
    print("TALK CHECKS PASSED")
finally:
    for p in procs:
        p.terminate()
    shutil.rmtree(tmp, ignore_errors=True)
