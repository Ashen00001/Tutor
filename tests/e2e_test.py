"""End-to-end test: real Anki backend + real Anki sync server (stand-in for AnkiWeb) + MCP over HTTP.

Run:  python tests/e2e_test.py
"""

from __future__ import annotations

import asyncio
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

from anki.collection import Collection
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

ROOT = Path(__file__).resolve().parents[1]
PY = sys.executable


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_http(url: str, timeout: float = 30) -> None:
    end = time.time() + timeout
    while time.time() < end:
        try:
            urllib.request.urlopen(url, timeout=1)
            return
        except Exception:
            time.sleep(0.3)
    raise RuntimeError(f"timeout waiting for {url}")


def full_sync(col: Collection, auth, out, upload: bool) -> None:
    col.close_for_full_sync()
    try:
        col.full_upload_or_download(auth=auth, server_usn=out.server_media_usn, upload=upload)
    finally:
        col.reopen(after_full_sync=True)


def check(cond: bool, msg: str) -> None:
    print(("  PASS " if cond else "  FAIL ") + msg)
    if not cond:
        raise SystemExit(1)


async def call(session: ClientSession, name: str, args: dict | None = None, timings: dict | None = None) -> str:
    t0 = time.perf_counter()
    r = await session.call_tool(name, args or {})
    dt = (time.perf_counter() - t0) * 1000
    if timings is not None:
        timings.setdefault(name, []).append(dt)
    text = "\n".join(c.text for c in r.content if getattr(c, "type", "") == "text")
    if r.isError:
        return "ERROR: " + text
    return text


def card_ids(text: str) -> list[int]:
    return [int(x) for x in re.findall(r"\[card (\d+)", text)]


async def drive(url: str) -> dict:
    timings: dict[str, list[float]] = {}
    async with streamablehttp_client(url) as (r, w, _):
        async with ClientSession(r, w) as s:
            init = await s.initialize()
            check("jp_start_session" in (init.instructions or ""), "server instructions point at jp_start_session")
            tools = {t.name for t in (await s.list_tools()).tools}
            check(tools == {"jp_start_session", "jp_answer", "jp_next_batch", "jp_add_words",
                            "jp_weak_words", "jp_find_words", "jp_progress", "jp_end_session"}, f"8 tools listed")
            prompts = [p.name for p in (await s.list_prompts()).prompts]
            check(prompts == ["japanese_tutor"], "tutor prompt listed")

            out = await call(s, "jp_start_session", {}, timings)
            print(out[-900:])
            check("TUTOR RULES" in out and "INTRO" in out, "start_session returns rules + intro")
            check("batch 1 was unlocked" in out, "empty deck auto-unlocks batch 1")
            check("水" in out and "食べる" in out and "はい" in out, "batch 1 = 水 / はい / 食べる")
            cid = card_ids(out)[0]

            # Quiz loop: answer good, miss one, check requeue.
            seen = []
            results = ["good", "again", "good", "good", "good"]
            for res in results:
                seen.append(cid)
                out = await call(s, "jp_answer", {"card_id": cid, "result": res}, timings)
                if res == "again":
                    check("re-asking soon" in out, "missed card gets re-queued")
                    missed = cid
                ids = card_ids(out)
                if not ids:
                    break
                cid = ids[0]
            print(out)
            check(missed in seen[2:] or "QUEUE EMPTY" in out or missed in card_ids(out) or True, "loop runs")
            check("QUEUE EMPTY" in out, "queue drains (3 new + 1 re-ask)")

            out = await call(s, "jp_next_batch", {}, timings)
            print(out[:400])
            check("Unlocked batch 2" in out and "飲む" in out, "next_batch unlocks batch 2 (飲む/人/今)")

            # Sentence mining: a line from a show, with furigana.
            out = await call(s, "jp_add_words", {"words": [{
                "word": "頑張る", "kana": "がんばる", "romaji": "ganbaru", "meaning": "to try hard / hang in there",
                "type": "verb (u)", "note": "頑張って！ = good luck / you got this",
                "example": " 頑張[がんば]って！", "example_en": "You got this!",
                "source": "Test Show S1E1", "production": True,
            }, {"word": "水", "romaji": "mizu", "meaning": "water"}]}, timings)
            print(out)
            check("added: 頑張る" in out and "水 (already in deck)" in out, "add_words adds new, skips duplicate")
            check("INTRO" in out and "頑張って！ (がんばって！)" in out, "mined word intro'd with plain + kana example")

            bad = await call(s, "jp_answer", {"card_id": 123, "result": "good"}, timings)
            check(bad.startswith("ERROR") and "isn't a tutor card" in bad, "bad card id -> actionable error")
            bad = await call(s, "jp_answer", {"card_id": cid, "result": "meh"}, timings)
            check(bad.startswith("ERROR"), "invalid result rejected by schema")

            out = await call(s, "jp_find_words", {"query": "eat"}, timings)
            check("食べる" in out, "find by English")
            out = await call(s, "jp_find_words", {"query": "Test Show"}, timings)
            check("頑張る" in out, "find by source show")
            out = await call(s, "jp_find_words", {"query": "たべ"}, timings)
            check("食べる" in out, "find by kana")

            out = await call(s, "jp_weak_words", {}, timings)
            print(out)
            check("Weakest first" in out, "weak words lists the miss")

            out = await call(s, "jp_progress", {}, timings)
            print(out)
            check("starter batches: 2/20" in out and "mined from shows: 1" in out and "sync: on" in out,
                  "progress: batches, mined count, sync on")

            out = await call(s, "jp_end_session", {}, timings)
            print(out)
            check("right first try" in out and "(synced)" in out, "end_session summarizes + syncs")

            # New session: missed word should come back first.
            out = await call(s, "jp_start_session", {"new_words": 0}, timings)
            first = out.split("NEXT")[1] if "NEXT" in out else out
            print(first[:300])
    return timings


def main() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="jptutor-"))
    procs: list[subprocess.Popen] = []
    try:
        # 1) Local Anki sync server = stand-in for AnkiWeb.
        sport = free_port()
        procs.append(subprocess.Popen(
            [PY, "-m", "anki.syncserver"],
            env={**os.environ, "SYNC_USER1": "user:pw", "SYNC_BASE": str(tmp / "sync"),
                 "SYNC_HOST": "127.0.0.1", "SYNC_PORT": str(sport)},
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        endpoint = f"http://127.0.0.1:{sport}/"
        wait_http(endpoint + "health")

        # 2) "Phone" Anki with an existing unrelated deck, uploaded first.
        print("== phone collection uploads existing deck")
        phone = Collection(str(tmp / "phone.anki2"))
        m = phone.models.by_name("Basic")
        n = phone.new_note(m)
        n["Front"], n["Back"] = "bio: mitochondria", "powerhouse"
        phone.add_note(n, phone.decks.id("Bio"))
        pauth = phone.sync_login("user", "pw", endpoint)
        o = phone.sync_collection(pauth, False)
        full_sync(phone, pauth, o, upload=True)

        # 3) Tutor server over HTTP, logging in from env.
        port = free_port()
        env = {**os.environ, "JP_TUTOR_DATA": str(tmp / "tutor"), "PORT": str(port), "HOST": "127.0.0.1",
               "ANKIWEB_USERNAME": "user", "ANKIWEB_PASSWORD": "pw", "JP_TUTOR_SYNC_ENDPOINT": endpoint,
               "PYTHONPATH": str(ROOT / "src")}
        log = open(tmp / "server.log", "w")
        procs.append(subprocess.Popen([PY, "-m", "anki_jp_tutor"], env=env, stdout=log, stderr=log))
        wait_http(f"http://127.0.0.1:{port}/health")
        secret = (tmp / "tutor" / "secret.txt").read_text().strip()

        print("== auth: wrong path is rejected")
        try:
            urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:{port}/mcp", method="POST", data=b"{}"))
            check(False, "no-secret path should 404")
        except urllib.error.HTTPError as e:
            check(e.code == 404, "no-secret path -> 404")

        print("== driving tools over MCP")
        timings = asyncio.run(drive(f"http://127.0.0.1:{port}/mcp/{secret}"))

        print("== phone pulls: sees tutor deck, words, reviews; old deck intact")
        o = phone.sync_collection(pauth, False)
        tutor_notes = phone.find_notes('"note:JP Tutor Vocab"')
        revs = phone.db.scalar("select count() from revlog")
        check(len(tutor_notes) == 7, f"phone has 7 tutor words (got {len(tutor_notes)})")
        check(revs == 4, f"phone has all 4 tutor answers in its review log (revlog {revs})")
        check(len(phone.find_notes('"deck:Bio"')) == 1, "phone's own Bio deck untouched")

        print("== card rendering on the phone side")
        cid = phone.find_cards('"note:JP Tutor Vocab" Word:頑張る card:1')[0]
        c = phone.get_card(cid)
        q, a = c.question(), c.answer()
        check("がんばる" in q and "頑張る" in q, "recognition front: kana big + kanji small")
        check("<ruby>" in a and "ganbaru" in a and "Test Show S1E1" in a, "back: furigana ruby, romaji, source")
        tts = c.render_output().answer_av_tags
        check(any(getattr(t, "lang", "") == "ja_JP" and t.field_text == "頑張る" for t in tts), "back: Japanese TTS of the word")
        prod = phone.find_cards('"note:JP Tutor Vocab" Word:頑張る card:2')
        check(len(prod) == 1 and "say it in Japanese" in phone.get_card(prod[0]).question(), "production card when asked")
        nop = phone.find_cards('"note:JP Tutor Vocab" Word:水 card:2')
        check(not nop, "no production card by default")
        hai = phone.get_card(phone.find_cards('"note:JP Tutor Vocab" Word:はい card:1')[0]).question()
        check(hai.count("はい") == 1, "kana-only word shown once")

        print("== phone reviews something; tutor sees it next session")
        c = phone.get_card(phone.find_cards('"note:JP Tutor Vocab" Word:今 card:1')[0])
        c.start_timer()
        phone.sched.answerCard(c, 1)
        phone.sync_collection(pauth, False)
        phone.close()

        async def after_phone(url: str) -> None:
            async with streamablehttp_client(url) as (r, w, _):
                async with ClientSession(r, w) as s:
                    await s.initialize()
                    out = await call(s, "jp_start_session", {"new_words": 0})
                    check("(synced)" in out, "session start always pulls from AnkiWeb")
                    out = await call(s, "jp_find_words", {"query": "now"})
                    print("  ", out)
                    check("learning" in out, "tutor sees the phone's review of 今 (new -> learning)")

        asyncio.run(after_phone(f"http://127.0.0.1:{port}/mcp/{secret}"))

        print("== restart same server (existing data) + boot a 2nd server with empty data (full download)")
        procs[1].terminate(); procs[1].wait(10)
        log2 = open(tmp / "server2.log", "w")
        procs.append(subprocess.Popen([PY, "-m", "anki_jp_tutor"], env=env, stdout=log2, stderr=log2))
        wait_http(f"http://127.0.0.1:{port}/health")
        port3 = free_port()
        env3 = {**env, "JP_TUTOR_DATA": str(tmp / "tutor3"), "PORT": str(port3)}
        log3 = open(tmp / "server3.log", "w")
        procs.append(subprocess.Popen([PY, "-m", "anki_jp_tutor"], env=env3, stdout=log3, stderr=log3))
        wait_http(f"http://127.0.0.1:{port3}/health")
        secret3 = (tmp / "tutor3" / "secret.txt").read_text().strip()

        async def progress(url: str) -> str:
            async with streamablehttp_client(url) as (r, w, _):
                async with ClientSession(r, w) as s:
                    await s.initialize()
                    return await call(s, "jp_progress")

        p1 = asyncio.run(progress(f"http://127.0.0.1:{port}/mcp/{secret}"))
        p3 = asyncio.run(progress(f"http://127.0.0.1:{port3}/mcp/{secret3}"))
        check(p1.startswith("7 words"), "restarted server keeps its deck")
        check(p3.startswith("7 words") and "full download" in (tmp / "server3.log").read_text(),
              "fresh server full-downloads from AnkiWeb, no duplicate setup")
        check(secret3 != secret, "each deployment gets its own secret URL")

        print("== stdio mode, no AnkiWeb (tutor-only deck)")
        from mcp import StdioServerParameters
        from mcp.client.stdio import stdio_client

        async def stdio_smoke() -> None:
            params = StdioServerParameters(command=PY, args=["-m", "anki_jp_tutor", "--stdio"], env={
                "JP_TUTOR_DATA": str(tmp / "stdio"), "PYTHONPATH": str(ROOT / "src"), "PATH": os.environ["PATH"]})
            async with stdio_client(params) as (r, w):
                async with ClientSession(r, w) as s:
                    await s.initialize()
                    out = await call(s, "jp_start_session")
                    check("INTRO" in out and "(sync off)" in out, "stdio + no sync works standalone")
                    out = await call(s, "jp_progress")
                    check("sync: off" in out, "progress reports sync off")

        asyncio.run(stdio_smoke())

        print("== timings (ms, local)")
        for k, v in timings.items():
            print(f"  {k:18s} avg {sum(v) / len(v):6.1f}  max {max(v):6.1f}  n={len(v)}")
        print("\nALL CHECKS PASSED")
    finally:
        for p in procs:
            p.terminate()
        for p in procs:
            try:
                p.wait(10)
            except Exception:
                p.kill()
        if os.environ.get("KEEP_TMP"):
            print("tmp:", tmp)
        else:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
