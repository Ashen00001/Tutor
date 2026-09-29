"""The voice web app: static files under /app/<secret>/ and a small JSON API under /api/<secret>/.

Quiz turns are graded instantly on the server (grade.py): no model in the loop.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Callable, TypeVar

from mcp.server.fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, RedirectResponse, Response

from . import grade
from .ask import AskClient
from .furigana import to_ruby_html
from .store import EASE, TutorError, TutorStore
from .sync import SyncManager

T = TypeVar("T")
WEB = Path(__file__).parent / "web"
STATIC = {
    "manifest.webmanifest": "application/manifest+json",
    "sw.js": "text/javascript",
    "icon.svg": "image/svg+xml",
    "icon-192.png": "image/png",
    "icon-512.png": "image/png",
    "apple-touch-icon.png": "image/png",
}


def decorate(item: dict | None, store: TutorStore) -> dict | None:
    """Add what the app needs to render/speak an item: ruby HTML and speech segments."""
    if not item:
        return item
    item = dict(item)
    if item["kind"] == "quiz":
        item["card"] = _with_html(item["card"], store)
        item["speech"] = grade.question_speech(item["card"])
    elif item["kind"] == "intro":
        item["cards"] = [_with_html(v, store) for v in item["cards"]]
        item["speech"] = grade.intro_speech(item["cards"])
        item["first"] = decorate(item["first"], store)
    return item


def _with_html(v: dict, store: TutorStore) -> dict:
    v = dict(v)
    raw = store.raw_example(v["card_id"])
    v["example_html"] = to_ruby_html(raw) if raw else ""
    return v


def register_web(mcp: FastMCP, store: TutorStore, sync: SyncManager, secret: str, ask: AskClient | None) -> None:
    app_base, api = f"/app/{secret}", f"/api/{secret}"

    async def run(fn: Callable[[], T]) -> T:
        return await asyncio.to_thread(fn)

    def err(e: Exception, code: int = 400) -> JSONResponse:
        return JSONResponse({"error": str(e)}, status_code=code)

    async def body(req: Request) -> dict[str, Any]:
        try:
            data = await req.json()
            return data if isinstance(data, dict) else {}
        except Exception:  # noqa: BLE001
            return {}

    # ------------------------------------------------------------ static
    @mcp.custom_route(app_base, methods=["GET"])
    async def app_redirect(_: Request) -> Response:
        return RedirectResponse(app_base + "/")

    @mcp.custom_route(app_base + "/", methods=["GET"])
    async def app_index(_: Request) -> Response:
        return FileResponse(WEB / "index.html", media_type="text/html",
                            headers={"Cache-Control": "no-cache", "X-Robots-Tag": "noindex",
                                     "Referrer-Policy": "no-referrer"})

    @mcp.custom_route(app_base + "/{name}", methods=["GET"])
    async def app_static(req: Request) -> Response:
        name = req.path_params["name"]
        if name not in STATIC:
            return Response(status_code=404)
        return FileResponse(WEB / name, media_type=STATIC[name], headers={"Cache-Control": "no-cache"})

    # --------------------------------------------------------------- api
    @mcp.custom_route(api + "/config", methods=["GET"])
    async def config(_: Request) -> Response:
        return JSONResponse({"ask": ask is not None, "deck": store.deck_name, "sync": sync.enabled})

    @mcp.custom_route(api + "/start", methods=["POST"])
    async def start(req: Request) -> Response:
        b = await body(req)
        new_words = max(0, min(6, int(b.get("new_words", 3))))

        def go() -> dict:
            sync.sync_now()
            d = store.start_session_data(new_words=new_words)
            d["item"] = decorate(d["item"], store)
            return d
        try:
            return JSONResponse(await run(go))
        except TutorError as e:
            return err(e)

    @mcp.custom_route(api + "/check", methods=["POST"])
    async def check(req: Request) -> Response:
        b = await body(req)
        try:
            v = await run(lambda: store.peek_card(int(b["card_id"])))
        except (TutorError, KeyError, ValueError) as e:
            return err(e)
        texts = [str(t) for t in ([b.get("text", "")] + list(b.get("alts") or [])) if str(t).strip()][:5]
        hinted, gave_up = bool(b.get("hinted")), bool(b.get("gave_up"))
        rank = {"correct": 3, "close": 2, "retry": 1, "wrong": 0}
        best = grade.check(v, texts[0] if texts else "", hinted=hinted, gave_up=gave_up or not texts)
        for t in texts[1:]:
            g = grade.check(v, t, hinted=hinted)
            if rank[g["verdict"]] > rank[best["verdict"]]:
                best = g
        best["card"] = _with_html(v, store)
        return JSONResponse(best)

    @mcp.custom_route(api + "/answer", methods=["POST"])
    async def answer(req: Request) -> Response:
        b = await body(req)
        result = b.get("result")
        if result not in EASE:
            return err(ValueError("result must be again/hard/good/easy"))

        def go() -> dict:
            d = store.answer_data(int(b["card_id"]), result)
            d["next"] = decorate(d["next"], store)
            d["hype"] = grade.hype(d["saved"]["in_a_row"]) if result != "again" else None
            return d
        try:
            return JSONResponse(await run(go))
        except (TutorError, KeyError, ValueError) as e:
            return err(e)

    @mcp.custom_route(api + "/batch", methods=["POST"])
    async def batch(_: Request) -> Response:
        def go() -> dict:
            d = store.next_batch_data(1)
            d["item"] = decorate(d["item"], store)
            return d
        return JSONResponse(await run(go))

    @mcp.custom_route(api + "/progress", methods=["GET"])
    async def progress(_: Request) -> Response:
        d = await run(store.progress_data)
        d["sync"] = sync.status()
        return JSONResponse(d)

    @mcp.custom_route(api + "/end", methods=["POST"])
    async def end(_: Request) -> Response:
        def go() -> dict:
            d = store.end_session_data()
            d["sync"] = sync.sync_now()
            return d
        return JSONResponse(await run(go))

    @mcp.custom_route(api + "/ask", methods=["POST"])
    async def ask_route(req: Request) -> Response:
        if ask is None:
            return err(RuntimeError("Ask mode is off. Add ANTHROPIC_API_KEY to .env to turn it on."), 404)
        b = await body(req)
        history = [m for m in (b.get("messages") or []) if isinstance(m, dict)]
        try:
            d = await ask.reply(history, b.get("card_id"))
        except Exception as e:  # noqa: BLE001
            return err(e, 502)
        d["item"] = await run(lambda: decorate(d["item"], store))
        return JSONResponse(d)
