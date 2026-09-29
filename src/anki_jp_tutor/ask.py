"""Optional 'ask anything' mode for the web app, powered by a small fast Claude model.

Enabled only when ANTHROPIC_API_KEY is set. The quiz loop never needs it.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

import httpx

from .store import TutorStore, WordInput

DEFAULT_MODEL = "claude-haiku-4-5-20251001"
MAX_TOOL_ROUNDS = 3

SYSTEM = """You're a chill casual-Japanese tutor inside a voice app for one learner (English speaker, beginner,
knows katakana, still building hiragana speed, wants real conversational Japanese, not keigo).

Replies are read aloud and shown in a small bubble:
- 1-3 short sentences. No markdown, no lists, no emoji.
- Write Japanese in kana/kanji (the app voices it in Japanese). Add romaji in parentheses only for new words.
- Be direct and casual, a bit hype, never preachy.

If they share a line from an anime/manga/game/song (or ask to save words): explain the line briefly, then call
add_words with 1-3 useful words from it. Use that exact line as `example` (kanji get Anki furigana with a space
before each kanji group, like " 水[みず] 飲[の]みたい。") and the show as `source` if they named it.
Only use the line they gave; don't quote more of the work.
"""

ADD_WORDS_TOOL = {
    "name": "add_words",
    "description": "Save 1-3 words to the learner's Anki deck so they get quizzed on them.",
    "input_schema": {
        "type": "object",
        "properties": {
            "words": {
                "type": "array", "minItems": 1, "maxItems": 5,
                "items": {
                    "type": "object",
                    "properties": {
                        "word": {"type": "string", "description": "natural written form, e.g. 食べる"},
                        "kana": {"type": "string", "description": "hiragana reading if word has kanji, else empty"},
                        "romaji": {"type": "string"},
                        "meaning": {"type": "string", "description": "short English meaning(s), slash-separated"},
                        "type": {"type": "string"},
                        "note": {"type": "string", "description": "one casual-usage tip"},
                        "example": {"type": "string"},
                        "example_en": {"type": "string"},
                        "source": {"type": "string"},
                    },
                    "required": ["word", "romaji", "meaning"],
                },
            }
        },
        "required": ["words"],
    },
}


class AskClient:
    def __init__(self, store: TutorStore, api_key: str, model: str | None = None, base_url: str | None = None):
        self.store = store
        self.api_key = api_key
        self.model = model or DEFAULT_MODEL
        self.base_url = (base_url or "https://api.anthropic.com").rstrip("/")
        self.http = httpx.AsyncClient(timeout=30)

    @classmethod
    def from_env(cls, store: TutorStore) -> "AskClient | None":
        key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
        if not key:
            return None
        return cls(store, key, os.environ.get("JP_TUTOR_MODEL") or None,
                   os.environ.get("JP_TUTOR_ANTHROPIC_BASE_URL") or None)

    async def _call(self, system: str, messages: list[dict]) -> dict:
        r = await self.http.post(
            f"{self.base_url}/v1/messages",
            headers={"x-api-key": self.api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
            json={"model": self.model, "max_tokens": 500, "system": system, "messages": messages,
                  "tools": [ADD_WORDS_TOOL]},
        )
        if r.status_code >= 400:
            raise RuntimeError(f"Claude API error {r.status_code}: {r.text[:200]}")
        return r.json()

    async def reply(self, history: list[dict], card_id: int | None = None) -> dict[str, Any]:
        context = []
        if card_id:
            try:
                v = await asyncio.to_thread(self.store.peek_card, card_id)
                context.append(f"Card on screen: {v['word']} ({v['kana'] or v['word']}, {v['romaji']}) = {v['meaning']}"
                               f" [{v['direction']}].")
            except Exception:  # noqa: BLE001
                pass
        prog = await asyncio.to_thread(self.store.progress_data)
        if prog.get("shakiest"):
            context.append("Words they keep missing: " + ", ".join(prog["shakiest"]) + ".")
        system = SYSTEM + ("\n" + "\n".join(context) if context else "")

        messages = [{"role": m["role"], "content": str(m["content"])[:2000]}
                    for m in history[-10:] if m.get("role") in ("user", "assistant") and m.get("content")]
        if not messages or messages[0]["role"] != "user":
            messages = [m for m in messages if m["role"] == "user"][-1:]

        added: list[str] = []
        item = None
        text_parts: list[str] = []
        for _ in range(MAX_TOOL_ROUNDS):
            resp = await self._call(system, messages)
            blocks = resp.get("content", [])
            text_parts = [b["text"] for b in blocks if b.get("type") == "text" and b.get("text", "").strip()]
            tool_uses = [b for b in blocks if b.get("type") == "tool_use"]
            if resp.get("stop_reason") != "tool_use" or not tool_uses:
                break
            messages.append({"role": "assistant", "content": blocks})
            results = []
            for tu in tool_uses:
                words = [WordInput(**{k: (w.get(k) or "") for k in (
                    "word", "kana", "romaji", "meaning", "type", "note", "example", "example_en", "source")})
                         for w in (tu.get("input", {}).get("words") or [])]
                d = await asyncio.to_thread(self.store.add_words_data, words, True)
                added += d["added"]
                item = d["item"] or item
                results.append({"type": "tool_result", "tool_use_id": tu["id"],
                                "content": f"added: {', '.join(d['added']) or 'none'}; skipped: {'; '.join(d['skipped']) or 'none'}"})
            messages.append({"role": "user", "content": results})
        reply = " ".join(text_parts).strip() or (f"Saved {', '.join(added)}." if added else "Hmm, try that again?")
        return {"reply": reply, "added": added, "item": item}
