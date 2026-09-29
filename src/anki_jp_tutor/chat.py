"""Conversation mode: a streaming Claude (Haiku by default) tutor that knows the learner's deck.

Enabled when ANTHROPIC_API_KEY is set. Streams text as it is written so the app can speak
the first sentence while the rest is still coming. Tools let the model save words and
count words the learner used correctly as Anki reviews.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any, AsyncIterator

import httpx

from .store import TutorError, TutorStore, WordInput

log = logging.getLogger("anki_jp_tutor.chat")

DEFAULT_MODEL = "claude-haiku-4-5-20251001"
MAX_TOOL_ROUNDS = 3

SCENARIOS = {
    "free": "",
    "konbini": "Role-play: you're a friendly convenience-store (コンビニ) clerk and they're a customer. Keep it simple.",
    "friend": "Role-play: you two just met at a party in Tokyo. You're a friendly local the same age. Small talk.",
    "anime": "Chat about anime and shows they like. Drop in real casual lines characters would say.",
    "ramen": "Role-play: you're the staff at a small ramen shop and they're ordering. Keep it simple.",
}

SYSTEM = """You're a chill Japanese conversation partner and tutor inside a voice app. One learner: English speaker, beginner
(knows katakana, still building hiragana speed), wants real casual spoken Japanese, not textbook keigo.

Everything you write is read aloud by a voice, then shown on screen:
- 1-3 short sentences per reply. No markdown, no lists, no emoji, no stage directions.
- Write Japanese in kana (hiragana/katakana), not kanji, so it's readable and pronounced right.
- When you introduce a word they don't know, put romaji in square brackets right after it, like みず[mizu].
  Bracketed romaji is shown but not spoken. Never write romaji anywhere else.
- Casual plain forms, the way friends actually talk. Keep the vibe relaxed and a bit hype, never preachy.

Teaching:
- Mostly English right now, with Japanese for words and short phrases they know. Add at most one new word per reply.
- Use words from their deck a lot, especially the shaky ones. Nudge them to answer in Japanese.
- If they say something in Japanese with a mistake, just say the natural version back once, then keep talking.
- End most replies with a quick question so the conversation keeps going.
- When they correctly use a deck word in Japanese, call record_answer(word, "good"). If you asked for a deck word
  and they didn't know it, call record_answer(word, "again") and tell them.
- If a useful new word comes up, or they ask to save one, call add_words (max 3 at a time).
- If they share a line from a show, explain it briefly and save 1-3 useful words from it with that line as the example.
"""

TOOLS = [
    {
        "name": "add_words",
        "description": "Save 1-3 words to the learner's Anki deck so they get quizzed on them later.",
        "input_schema": {
            "type": "object",
            "properties": {
                "words": {
                    "type": "array", "minItems": 1, "maxItems": 3,
                    "items": {
                        "type": "object",
                        "properties": {
                            "word": {"type": "string", "description": "natural written form, e.g. 食べる"},
                            "kana": {"type": "string", "description": "hiragana reading if the word has kanji, else empty"},
                            "romaji": {"type": "string"},
                            "meaning": {"type": "string", "description": "short English meaning(s), slash-separated"},
                            "type": {"type": "string"},
                            "note": {"type": "string", "description": "one casual-usage tip"},
                            "example": {"type": "string", "description": "short casual sentence; kanji as ' 漢字[かんじ]'"},
                            "example_en": {"type": "string"},
                            "source": {"type": "string", "description": "show name if it came from one"},
                        },
                        "required": ["word", "romaji", "meaning"],
                    },
                }
            },
            "required": ["words"],
        },
    },
    {
        "name": "record_answer",
        "description": "Log how the learner did with one of their deck words during conversation (counts as an Anki review).",
        "input_schema": {
            "type": "object",
            "properties": {
                "word": {"type": "string", "description": "the deck word exactly as listed, e.g. 水 or うん"},
                "result": {"type": "string", "enum": ["good", "hard", "again"]},
            },
            "required": ["word", "result"],
        },
    },
]


class ChatClient:
    def __init__(self, store: TutorStore, api_key: str, model: str | None = None, base_url: str | None = None):
        self.store = store
        self.api_key = api_key
        self.model = model or DEFAULT_MODEL
        self.base_url = (base_url or "https://api.anthropic.com").rstrip("/")
        self.http = httpx.AsyncClient(timeout=httpx.Timeout(30, read=60))

    @classmethod
    def from_env(cls, store: TutorStore) -> "ChatClient | None":
        key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
        if not key:
            return None
        return cls(store, key, os.environ.get("JP_TUTOR_MODEL") or None,
                   os.environ.get("JP_TUTOR_ANTHROPIC_BASE_URL") or None)

    # ------------------------------------------------------------ context
    async def _system(self, scenario: str, card_id: int | None) -> str:
        known = await asyncio.to_thread(self.store.known_words, 120)
        prog = await asyncio.to_thread(self.store.progress_data)
        parts = [SYSTEM]
        if known:
            parts.append("Their deck (word = meaning): " + "; ".join(f"{w} = {m}" for w, m in known) + ".")
        if prog.get("shakiest"):
            parts.append("Words they keep missing (work these in): " + ", ".join(prog["shakiest"]) + ".")
        if card_id:
            try:
                v = await asyncio.to_thread(self.store.peek_card, card_id)
                parts.append(f"They were just quizzed on {v['word']} ({v['meaning']}).")
            except TutorError:
                pass
        if SCENARIOS.get(scenario):
            parts.append(SCENARIOS[scenario])
        return "\n".join(parts)

    @staticmethod
    def _messages(history: list[dict]) -> list[dict]:
        msgs = [{"role": m["role"], "content": str(m["content"])[:2000]}
                for m in history[-16:] if m.get("role") in ("user", "assistant") and str(m.get("content", "")).strip()]
        while msgs and msgs[0]["role"] != "user":
            msgs.pop(0)
        merged: list[dict] = []
        for m in msgs:  # the API wants alternating roles
            if merged and merged[-1]["role"] == m["role"]:
                merged[-1]["content"] += "\n" + m["content"]
            else:
                merged.append(dict(m))
        return merged

    # ------------------------------------------------------------- tools
    def _run_tool(self, name: str, args: dict) -> tuple[str, dict]:
        if name == "add_words":
            words = [WordInput(**{k: str(w.get(k) or "") for k in (
                "word", "kana", "romaji", "meaning", "type", "note", "example", "example_en", "source")})
                for w in (args.get("words") or [])[:3]]
            d = self.store.add_words_data(words, quiz_now=False)
            return (f"added: {', '.join(d['added']) or 'none'}; skipped: {'; '.join(d['skipped']) or 'none'}",
                    {"t": "saved", "words": d["added"]})
        if name == "record_answer":
            try:
                d = self.store.answer_by_word(str(args.get("word", "")), str(args.get("result", "good")))
                return f"recorded {d['word']} as {d['result']}", {"t": "reviewed", **d}
            except TutorError as e:
                return f"error: {e}", {}
        return "unknown tool", {}

    # ---------------------------------------------------------- streaming
    async def _stream_round(self, system: str, messages: list[dict]) -> AsyncIterator[dict]:
        """Yields {'delta': text} while streaming, then {'blocks': [...], 'stop': reason}."""
        body = {"model": self.model, "max_tokens": 400, "system": system, "messages": messages,
                "tools": TOOLS, "stream": True}
        headers = {"x-api-key": self.api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"}
        blocks: dict[int, dict] = {}
        stop = None
        async with self.http.stream("POST", f"{self.base_url}/v1/messages", json=body, headers=headers) as r:
            if r.status_code >= 400:
                raise RuntimeError(f"Claude API error {r.status_code}: {(await r.aread())[:200]!r}")
            async for line in r.aiter_lines():
                if not line.startswith("data:"):
                    continue
                ev = json.loads(line[5:].strip() or "{}")
                typ = ev.get("type")
                if typ == "content_block_start":
                    cb = dict(ev["content_block"])
                    if cb.get("type") == "tool_use":
                        cb["_json"] = ""
                    blocks[ev["index"]] = cb
                elif typ == "content_block_delta":
                    d = ev["delta"]; cb = blocks[ev["index"]]
                    if d.get("type") == "text_delta":
                        cb["text"] = cb.get("text", "") + d["text"]
                        yield {"delta": d["text"]}
                    elif d.get("type") == "input_json_delta":
                        cb["_json"] += d.get("partial_json", "")
                elif typ == "message_delta":
                    stop = ev.get("delta", {}).get("stop_reason", stop)
                elif typ == "error":
                    raise RuntimeError(ev.get("error", {}).get("message", "stream error"))
        out = []
        for i in sorted(blocks):
            cb = blocks[i]
            if cb.get("type") == "tool_use":
                raw = cb.pop("_json", "")
                cb["input"] = json.loads(raw) if raw.strip() else {}
            out.append(cb)
        yield {"blocks": out, "stop": stop}

    async def converse(self, history: list[dict], scenario: str = "free", card_id: int | None = None) -> AsyncIterator[dict]:
        """Events: {t: delta, text} · {t: saved, words} · {t: reviewed, word, result} · {t: done, text}."""
        system = await self._system(scenario, card_id)
        messages = self._messages(history)
        if not messages:
            messages = [{"role": "user", "content": "(They opened conversation mode. Greet them in one line and ask something easy.)"}]
        full = ""
        for _ in range(MAX_TOOL_ROUNDS):
            blocks, stop = [], None
            async for ev in self._stream_round(system, messages):
                if "delta" in ev:
                    full += ev["delta"]
                    yield {"t": "delta", "text": ev["delta"]}
                else:
                    blocks, stop = ev["blocks"], ev["stop"]
            uses = [b for b in blocks if b.get("type") == "tool_use"]
            if stop != "tool_use" or not uses:
                break
            messages.append({"role": "assistant", "content": blocks})
            results = []
            for tu in uses:
                summary, event = await asyncio.to_thread(self._run_tool, tu["name"], tu.get("input") or {})
                if event:
                    yield event
                results.append({"type": "tool_result", "tool_use_id": tu["id"], "content": summary})
            messages.append({"role": "user", "content": results})
            if full and not full.endswith((" ", "\n")):
                full += " "
                yield {"t": "delta", "text": " "}
        yield {"t": "done", "text": full.strip()}
