"""MCP tools. Every quiz turn is ONE call (jp_answer saves + returns the next card) to keep chats fast."""

from __future__ import annotations

import asyncio
from typing import Annotated, Callable, Literal, TypeVar

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from pydantic import BaseModel, Field
from starlette.requests import Request
from starlette.responses import JSONResponse

from . import content
from .ask import AskClient
from .store import TutorError, TutorStore, WordInput
from .sync import SyncManager
from .webapp import register_web

T = TypeVar("T")

WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False)
READ = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)


class WordItem(BaseModel):
    word: str = Field(max_length=40, description="Natural written form: 食べる, マジ, うん")
    romaji: str = Field(max_length=60, description="taberu")
    meaning: str = Field(max_length=80, description="Short English meaning: 'to eat'")
    kana: str = Field("", max_length=40, description="Hiragana reading ONLY if word has kanji (たべる); else empty")
    type: str = Field("", max_length=40, description="verb (ru) / verb (u) / noun / adjective (i) / slang ...")
    note: str = Field("", max_length=160, description="One casual-usage tip, max one sentence")
    example: str = Field("", max_length=200, description=(
        "One short casual sentence. Kanji get Anki furigana with a space before each kanji group: "
        "' 水[みず] 飲[の]みたい。'. For a line mined from a show, use just that one line."))
    example_en: str = Field("", max_length=200, description="English for the example")
    source: str = Field("", max_length=80, description="Where it came from, e.g. 'Frieren S1E3' (empty if none)")
    production: bool | None = Field(None, description="Also make an English→Japanese card (default: server setting)")


def build_server(store: TutorStore, sync: SyncManager, *, host: str = "0.0.0.0", port: int = 8000,
                 path: str = "/mcp", secret: str | None = None, ask: "AskClient | None" = None) -> FastMCP:
    mcp = FastMCP(
        name="anki-jp-tutor",
        instructions=content.SERVER_INSTRUCTIONS,
        host=host,
        port=port,
        streamable_http_path=path,
        stateless_http=True,
        json_response=True,
        # Auth is the unguessable secret in the path; the server sits behind a proxy/tunnel
        # whose Host header we can't predict, so DNS-rebinding host checks are off.
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )

    async def run(fn: Callable[[], T]) -> T:
        try:
            return await asyncio.to_thread(fn)
        except TutorError as e:
            raise ToolError(str(e)) from e
        except ToolError:
            raise
        except Exception as e:  # noqa: BLE001
            raise ToolError(f"Anki error: {e}") from e

    @mcp.tool(
        name="jp_start_session",
        title="Start Japanese session",
        description=(
            "Start (or restart) a tutoring session. Call this FIRST. Returns the tutoring rules, "
            "a one-line stats summary, and the first item (an INTRO of new words or a quiz card). "
            "Queue order: weakest words, then due reviews, then new words (3 at a time). "
            "If nothing is waiting it auto-unlocks the next 3 starter words."
        ),
        annotations=WRITE,
        structured_output=False,
    )
    async def jp_start_session(
        new_words: Annotated[int, Field(ge=0, le=6, description="Max new words this session")] = 3,
        review_limit: Annotated[int, Field(ge=0, le=40, description="Max review cards")] = 12,
    ) -> str:
        def go() -> str:
            pulled = sync.sync_now()  # always pull: they may have reviewed on the phone app
            return store.start_session(new_words, review_limit) + f"\n({pulled})"
        return await run(go)

    @mcp.tool(
        name="jp_answer",
        title="Record answer + get next card",
        description=(
            "Record how the learner did on a card (saved to Anki's scheduler) and get the NEXT card "
            "in the same call. result: again = wrong/blank, hard = right but slow/hinted/partial, "
            "good = right, easy = instant. Missed cards are re-queued a couple of cards later automatically."
        ),
        annotations=WRITE,
        structured_output=False,
    )
    async def jp_answer(
        card_id: Annotated[int, Field(description="card id from the item just asked")],
        result: Literal["again", "hard", "good", "easy"],
    ) -> str:
        return await run(lambda: store.answer(card_id, result))

    @mcp.tool(
        name="jp_next_batch",
        title="Unlock next starter words",
        description=(
            "Add the next batch of 3 casual starter words (60-word curriculum) and put them at the front "
            "of the queue. Returns an INTRO item plus the first quiz."
        ),
        annotations=WRITE,
        structured_output=False,
    )
    async def jp_next_batch(
        batches: Annotated[int, Field(ge=1, le=3, description="How many 3-word batches")] = 1,
    ) -> str:
        return await run(lambda: store.next_batch(batches))

    @mcp.tool(
        name="jp_add_words",
        title="Add words (incl. from anime/manga lines)",
        description=(
            "Add 1-10 words to the deck, e.g. words the learner asked about or picked from a line in a show "
            "they're watching (put that single line in `example` and the show in `source`). Skips duplicates. "
            "With quiz_now, they're introduced and quizzed next."
        ),
        annotations=WRITE,
        structured_output=False,
    )
    async def jp_add_words(
        words: Annotated[list[WordItem], Field(min_length=1, max_length=10)],
        quiz_now: bool = True,
    ) -> str:
        items = [WordInput(**w.model_dump()) for w in words]
        return await run(lambda: store.add_words(items, quiz_now))

    @mcp.tool(
        name="jp_weak_words",
        title="Weakest words",
        description="List the words the learner struggles with most (recent misses, lapses, accuracy), with reasons.",
        annotations=READ,
        structured_output=False,
    )
    async def jp_weak_words(limit: Annotated[int, Field(ge=1, le=20)] = 5) -> str:
        return await run(lambda: store.weak_words(limit))

    @mcp.tool(
        name="jp_find_words",
        title="Search deck",
        description="Search the deck by Japanese, kana, romaji, English meaning, or source show. Returns status and next review.",
        annotations=READ,
        structured_output=False,
    )
    async def jp_find_words(
        query: Annotated[str, Field(min_length=1, max_length=60)],
        limit: Annotated[int, Field(ge=1, le=30)] = 10,
    ) -> str:
        return await run(lambda: store.find_words(query, limit))

    @mcp.tool(
        name="jp_progress",
        title="Progress",
        description="Word counts, what's due, today's answers, streak, starter-batch progress, shakiest words, sync status.",
        annotations=READ,
        structured_output=False,
    )
    async def jp_progress() -> str:
        return await run(lambda: store.progress() + "\n" + sync.status())

    @mcp.tool(
        name="jp_end_session",
        title="End session",
        description="End the session: short summary (accuracy, missed words) and push progress to AnkiWeb if sync is on.",
        annotations=WRITE,
        structured_output=False,
    )
    async def jp_end_session() -> str:
        def go() -> str:
            summary = store.end_session()
            return f"{summary}\n({sync.sync_now()})"
        return await run(go)

    @mcp.prompt(name="japanese_tutor", title="Japanese tutor", description="Start a casual Japanese session")
    def japanese_tutor() -> str:
        return "Let's do some Japanese. Start a session with jp_start_session and follow its rules."

    @mcp.custom_route("/health", methods=["GET"])
    async def health(_: Request) -> JSONResponse:
        return JSONResponse({"ok": True})

    if secret:
        register_web(mcp, store, sync, secret, ask)

    return mcp
