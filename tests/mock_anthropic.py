"""Tiny stand-in for the Claude Messages API with streaming + tool use, for tests.

python tests/mock_anthropic.py PORT
- no user text yet      -> greeting
- user mentions water   -> text, then record_answer(水, good) tool call, then (after tool_result) a reply
- user says "save"      -> add_words tool call, then a reply
Requests are logged at GET /calls.
"""

from __future__ import annotations

import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

CALLS: list[dict] = []


def sse(events: list[dict]) -> bytes:
    return b"".join(f"event: {e['type']}\ndata: {json.dumps(e, ensure_ascii=False)}\n\n".encode() for e in events)


def text_msg(chunks: list[str], stop: str = "end_turn", tool: dict | None = None) -> list[dict]:
    ev = [{"type": "message_start", "message": {"id": "m", "role": "assistant", "content": []}},
          {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}}]
    ev += [{"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": c}} for c in chunks]
    ev.append({"type": "content_block_stop", "index": 0})
    if tool:
        raw = json.dumps(tool["input"], ensure_ascii=False)
        ev += [{"type": "content_block_start", "index": 1, "content_block": {"type": "tool_use", "id": "tu_1", "name": tool["name"], "input": {}}},
               {"type": "content_block_delta", "index": 1, "delta": {"type": "input_json_delta", "partial_json": raw[:10]}},
               {"type": "content_block_delta", "index": 1, "delta": {"type": "input_json_delta", "partial_json": raw[10:]}},
               {"type": "content_block_stop", "index": 1}]
    ev += [{"type": "message_delta", "delta": {"stop_reason": stop}}, {"type": "message_stop"}]
    return ev


class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def do_GET(self):
        body = json.dumps(CALLS, ensure_ascii=False).encode()
        self.send_response(200); self.send_header("content-type", "application/json"); self.send_header("content-length", str(len(body)))
        self.end_headers(); self.wfile.write(body)

    def do_POST(self):
        req = json.loads(self.rfile.read(int(self.headers["content-length"])))
        if self.headers.get("x-api-key") != "test-key":
            self.send_response(401); self.send_header("content-length", "0"); self.end_headers(); return
        CALLS.append({"system": req["system"], "messages": req["messages"], "model": req["model"], "tools": [t["name"] for t in req["tools"]]})
        last = req["messages"][-1]
        content = last["content"]
        if isinstance(content, list):  # tool results came back
            events = text_msg(["みず[mizu] ", "のみたい。 ", "Nice, what else do you want? "])
        elif "water" in content.lower():
            events = text_msg(["Yesss, ", "you used it right. "], stop="tool_use", tool={"name": "record_answer", "input": {"word": "水", "result": "good"}})
        elif "save" in content.lower():
            events = text_msg(["Saving that. "], stop="tool_use", tool={"name": "add_words", "input": {"words": [
                {"word": "頑張る", "kana": "がんばる", "romaji": "ganbaru", "meaning": "to try hard", "example": " 頑張[がんば]って！", "example_en": "You got this!"}]}})
        else:
            events = text_msg(["Yo! ", "What's up? ", "Ready to practice some Japanese?"])
        body = sse(events)
        self.send_response(200); self.send_header("content-type", "text/event-stream"); self.send_header("content-length", str(len(body)))
        self.end_headers(); self.wfile.write(body)


if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1])), H).serve_forever()
