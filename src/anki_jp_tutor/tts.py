"""ElevenLabs text-to-speech with an on-disk cache.

The API key stays on the server; the app fetches audio from /api/<secret>/tts.
The tutor repeats the same words and lines a lot, so every clip is cached by
(model, voice, text): repeats are instant and cost no credits.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re
import time
from pathlib import Path

import httpx

log = logging.getLogger("anki_jp_tutor.tts")

DEFAULT_MODEL = "eleven_v4_turbo"  # low-latency v4 variant (released 2026-09-28)
PREFERRED_VOICES = ["Jessica", "Sarah", "Aria", "Laura", "Lily", "Alice", "Charlotte"]
VOICE_ID = re.compile(r"^[A-Za-z0-9]{8,40}$")
MAX_TEXT = 400
CACHE_LIMIT_BYTES = 300 * 1024 * 1024


class TTSError(Exception):
    pass


class ElevenLabsTTS:
    def __init__(self, api_key: str, cache_dir: Path, model: str | None = None, voice_id: str | None = None,
                 base_url: str | None = None, stability: float = 0.5, similarity: float = 0.8):
        self.api_key = api_key
        self.model = model or DEFAULT_MODEL
        self.default_voice = voice_id or None
        self.base_url = (base_url or "https://api.elevenlabs.io").rstrip("/")
        self.stability, self.similarity = stability, similarity
        self.stt_model = os.environ.get("ELEVENLABS_STT_MODEL") or "scribe_v2"
        self.cache = cache_dir
        self.cache.mkdir(parents=True, exist_ok=True)
        self.http = httpx.AsyncClient(timeout=20, headers={"xi-api-key": api_key})
        self._voices: list[dict] | None = None
        self._voices_at = 0.0
        self._voices_lock = asyncio.Lock()

    @classmethod
    def from_env(cls, data_dir: Path) -> "ElevenLabsTTS | None":
        key = os.environ.get("ELEVENLABS_API_KEY", "").strip()
        if not key:
            return None
        return cls(
            key, data_dir / "tts-cache",
            model=os.environ.get("ELEVENLABS_MODEL") or None,
            voice_id=os.environ.get("ELEVENLABS_VOICE_ID") or None,
            base_url=os.environ.get("ELEVENLABS_BASE_URL") or None,
        )

    # ---------------------------------------------------------------- voices
    async def voices(self) -> list[dict]:
        async with self._voices_lock:
            return await self._load_voices()

    async def _load_voices(self) -> list[dict]:
        if self._voices is not None and time.time() - self._voices_at < 600:
            return self._voices
        r = await self.http.get(f"{self.base_url}/v1/voices")
        if r.status_code >= 400:
            raise TTSError(f"ElevenLabs voices error {r.status_code}: {r.text[:160]}")
        out = []
        for v in r.json().get("voices", []):
            labels = v.get("labels") or {}
            out.append({"id": v.get("voice_id"), "name": v.get("name", "?"), "category": v.get("category", ""),
                        "accent": labels.get("accent", ""), "gender": labels.get("gender", ""),
                        "description": labels.get("description") or labels.get("descriptive", "")})
        self._voices, self._voices_at = [v for v in out if v["id"]], time.time()
        return self._voices

    async def default_voice_id(self) -> str:
        if self.default_voice:
            return self.default_voice
        vs = await self.voices()
        for name in PREFERRED_VOICES:
            for v in vs:
                if v["name"].split()[0].lower() == name.lower():
                    self.default_voice = v["id"]
                    return v["id"]
        if not vs:
            raise TTSError("No ElevenLabs voices on this account. Set ELEVENLABS_VOICE_ID.")
        self.default_voice = vs[0]["id"]
        return self.default_voice

    # ------------------------------------------------------------ synthesis
    def _path(self, voice: str, text: str) -> Path:
        h = hashlib.sha256(f"{self.model}|{voice}|{self.stability}|{self.similarity}|{text}".encode()).hexdigest()
        return self.cache / f"{h[:2]}" / f"{h}.mp3"

    async def speak(self, text: str, voice: str | None = None) -> tuple[bytes, bool]:
        """Returns (mp3 bytes, cache_hit)."""
        text = " ".join(text.split())[:MAX_TEXT]
        if not text:
            raise TTSError("Nothing to say.")
        if voice and not VOICE_ID.match(voice):
            raise TTSError("Bad voice id.")
        voice = voice or await self.default_voice_id()
        path = self._path(voice, text)
        if path.exists():
            path.touch()
            return path.read_bytes(), True
        r = await self.http.post(
            f"{self.base_url}/v1/text-to-speech/{voice}",
            params={"output_format": "mp3_44100_128"},
            json={"text": text, "model_id": self.model,
                  "voice_settings": {"stability": self.stability, "similarity_boost": self.similarity}},
        )
        if r.status_code >= 400:
            raise TTSError(f"ElevenLabs error {r.status_code}: {r.text[:200]}")
        audio = r.content
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_bytes(audio)
        tmp.replace(path)
        self._prune()
        return audio, False

    async def transcribe(self, audio: bytes, content_type: str) -> dict:
        """Speech to text with Scribe (auto-detects the language, handles mixed EN/JP)."""
        if len(audio) < 800:
            return {"text": "", "language": None}
        ext = {"audio/webm": "webm", "audio/ogg": "ogg", "audio/mp4": "mp4", "audio/mpeg": "mp3", "audio/wav": "wav"}.get(
            content_type.split(";")[0].strip(), "webm")
        r = await self.http.post(
            f"{self.base_url}/v1/speech-to-text",
            data={"model_id": self.stt_model, "timestamps_granularity": "none"},
            files={"file": (f"speech.{ext}", audio, content_type or "application/octet-stream")},
        )
        if r.status_code >= 400:
            raise TTSError(f"ElevenLabs speech-to-text error {r.status_code}: {r.text[:200]}")
        d = r.json()
        return {"text": (d.get("text") or "").strip(), "language": d.get("language_code")}

    def _prune(self) -> None:
        files = [f for f in self.cache.rglob("*.mp3")]
        total = sum(f.stat().st_size for f in files)
        if total <= CACHE_LIMIT_BYTES:
            return
        for f in sorted(files, key=lambda f: f.stat().st_mtime):
            total -= f.stat().st_size
            f.unlink(missing_ok=True)
            if total <= CACHE_LIMIT_BYTES * 0.8:
                break
