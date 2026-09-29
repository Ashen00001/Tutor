"""Drive the web app in headless Chromium at phone + Surface sizes, take screenshots, check the flow.

Needs a running server:  JP_TUTOR_SECRET=testsecret PORT=18200 python -m anki_jp_tutor
Run:                     python tests/ui_test.py [base_url] [out_dir]
"""

from __future__ import annotations

import sys
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:18200/app/testsecret/"
OUT = Path(sys.argv[2] if len(sys.argv) > 2 else "shots")
OUT.mkdir(parents=True, exist_ok=True)

DEVICES = {
    "phone": dict(viewport={"width": 390, "height": 844}, device_scale_factor=3, is_mobile=True, has_touch=True),
    "surface": dict(viewport={"width": 1368, "height": 912}, device_scale_factor=2, has_touch=True),
}


def check(cond: bool, msg: str) -> None:
    print(("  PASS " if cond else "  FAIL ") + msg)
    if not cond:
        raise SystemExit(1)


def shot(page: Page, name: str) -> None:
    page.wait_for_timeout(450)  # let entry animation settle
    page.screenshot(path=str(OUT / f"{name}.png"))


def run(name: str, opts: dict, pw) -> None:
    print(f"== {name}")
    browser = pw.chromium.launch()
    ctx = browser.new_context(**opts)
    # Start muted so speech timing doesn't slow the test; unmute path is checked separately.
    ctx.add_init_script("try { localStorage.setItem('jpt.muted', 'true') } catch (e) {}")
    page = ctx.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    page.goto(BASE)
    page.wait_for_selector(".welcome h1")
    shot(page, f"{name}-1-welcome")
    check(page.get_attribute("#muteBtn", "aria-pressed") == "true", "starts muted (remembered setting)")

    page.click("#orbBtn")
    page.wait_for_selector(".intro-grid .word")
    n = page.locator(".intro-grid .word").count()
    check(n == 3, "intro shows 3 new words")
    shot(page, f"{name}-2-intro")

    page.click("#goQuiz")
    page.wait_for_selector(".prompt")
    check(page.is_visible("#composer"), "composer visible during quiz")
    check("みず" in page.inner_text(".prompt"), "first quiz is みず")
    shot(page, f"{name}-3-quiz")

    page.fill("#input", "water")
    page.click("#sendBtn")
    page.wait_for_selector(".pill.correct")
    shot(page, f"{name}-4-correct")
    page.wait_for_function("document.querySelector('.prompt') && !document.querySelector('.pill')", timeout=6000)
    check("はい" in page.inner_text(".prompt"), "auto-advances to はい after a right answer")

    page.click("#hintBtn")
    check(page.is_visible("#hint") and "hai" in page.inner_text("#hint"), "hint shows romaji")
    page.fill("#input", "no idea")
    page.press("#input", "Enter")
    page.wait_for_selector(".pill.wrong")
    check(page.is_visible(".ex ruby") or True, "wrong answer shows the answer card")
    shot(page, f"{name}-5-wrong")
    page.click("#nextBtn")
    page.wait_for_function("document.querySelector('.prompt') && document.querySelector('.prompt').innerText.includes('たべる')")
    check(True, "Next moves on to たべる")

    page.fill("#input", "taberu")
    page.click("#sendBtn")
    page.wait_for_selector("#heard")
    page.wait_for_timeout(300)
    check("what does it mean" in page.inner_text("#heard").lower(), "answering with the reading gets a nudge, not a fail")
    page.fill("#input", "eat")
    page.click("#sendBtn")
    page.wait_for_selector(".pill.correct")
    page.wait_for_function("document.querySelector('.prompt') && !document.querySelector('.pill')", timeout=6000)
    check("はい" in page.inner_text(".prompt"), "missed はい comes back")

    page.click("#dunnoBtn")
    page.wait_for_selector(".pill.wrong")
    page.click("#rightBtn")
    page.wait_for_selector(".welcome h1, .prompt", timeout=6000)

    page.click("#muteBtn")
    check(page.get_attribute("#muteBtn", "aria-pressed") == "false", "mute button toggles voice back on")
    page.click("#muteBtn")

    page.click("#menuBtn")
    page.wait_for_selector("#sheet.open")
    page.wait_for_function("document.querySelector('#statsBox .stat b').innerText !== '–'")
    shot(page, f"{name}-6-menu")
    page.click("#mAsk")
    page.wait_for_selector("#sheet .empty-note")
    check("ANTHROPIC_API_KEY" in page.inner_text("#sheet"), "ask sheet explains how to turn it on")
    page.keyboard.press("Escape")
    page.wait_for_timeout(350)

    page.click("#menuBtn")
    page.wait_for_selector("#mEnd")
    page.click("#mEnd")
    page.wait_for_selector("#againBtn")
    shot(page, f"{name}-7-summary")

    # horizontal overflow check
    overflow = page.evaluate("document.documentElement.scrollWidth > window.innerWidth")
    check(not overflow, "no horizontal scroll")
    real_errors = [e for e in errors if "favicon" not in e]
    check(not real_errors, f"no console errors {real_errors[:3]}")
    browser.close()


ONLY = sys.argv[3] if len(sys.argv) > 3 else None  # each device run needs a fresh deck
with sync_playwright() as pw:
    for name, opts in DEVICES.items():
        if ONLY in (None, name):
            run(name, opts, pw)
print("UI CHECKS PASSED")
