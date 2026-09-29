"""Tiny stand-in for the ElevenLabs API (voices + text-to-speech) used by tests.

python tests/mock_elevenlabs.py PORT   -> serves /v1/voices and /v1/text-to-speech/<voice_id>
Returns a WAV tone whose length follows the text length; counts synth calls at /calls.
"""

from __future__ import annotations

import io
import json
import math
import struct
import sys
import wave
from http.server import BaseHTTPRequestHandler, HTTPServer

CALLS: list[dict] = []
VOICES = [
    {"voice_id": "aaaaaaaaaaaaaaaaaaaa", "name": "Jessica", "category": "premade", "labels": {"accent": "american", "gender": "female"}},
    {"voice_id": "bbbbbbbbbbbbbbbbbbbb", "name": "Liam", "category": "premade", "labels": {"accent": "american", "gender": "male"}},
]


def tone(seconds: float) -> bytes:
    rate, buf = 22050, io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate)
        frames = b"".join(struct.pack("<h", int(9000 * math.sin(2 * math.pi * 220 * i / rate) * (0.5 + 0.5 * math.sin(i / 900))))
                          for i in range(int(rate * seconds)))
        w.writeframes(frames)
    return buf.getvalue()


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):  # quiet
        pass

    def _json(self, code: int, obj) -> None:
        body = json.dumps(obj).encode()
        self.send_response(code); self.send_header("content-type", "application/json"); self.send_header("content-length", str(len(body)))
        self.end_headers(); self.wfile.write(body)

    def do_GET(self):
        if self.headers.get("xi-api-key") != "test-key" and self.path != "/calls":
            return self._json(401, {"detail": "bad key"})
        if self.path.startswith("/v1/voices"):
            return self._json(200, {"voices": VOICES})
        if self.path == "/calls":
            return self._json(200, CALLS)
        self._json(404, {})

    def do_POST(self):
        if self.headers.get("xi-api-key") != "test-key":
            return self._json(401, {"detail": "bad key"})
        if self.path.startswith("/v1/speech-to-text"):
            n = int(self.headers.get("content-length", 0)); raw = self.rfile.read(n)
            CALLS.append({"stt": True, "bytes": n, "has_model": b"scribe_v2" in raw})
            return self._json(200, {"text": "I want some water", "language_code": "en", "language_probability": 0.97})
        if not self.path.startswith("/v1/text-to-speech/"):
            return self._json(404, {})
        body = json.loads(self.rfile.read(int(self.headers["content-length"])))
        CALLS.append({"voice": self.path.split("/")[3].split("?")[0], "model": body.get("model_id"), "text": body.get("text")})
        audio = tone(0.4 + 0.06 * len(body.get("text", "")))
        self.send_response(200); self.send_header("content-type", "audio/mpeg"); self.send_header("content-length", str(len(audio)))
        self.end_headers(); self.wfile.write(audio)


if __name__ == "__main__":
    HTTPServer(("127.0.0.1", int(sys.argv[1])), H).serve_forever()
