"""Anki collection wrapper: setup, tutoring session queue, answers, stats.

Everything touching the collection goes through `self.lock` (the Rust backend is not
thread-safe, and the sync manager runs on a background thread).
"""

from __future__ import annotations

import math
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Callable, Literal

from anki.collection import Collection, SearchNode  # must be imported before anki.cards
from anki.cards import Card
from anki.errors import NotFoundError
from anki.utils import ids2str

from . import content
from .furigana import has_ruby, is_all_kana, to_kana, to_plain

Result = Literal["again", "hard", "good", "easy"]
EASE: dict[str, Literal[1, 2, 3, 4]] = {"again": 1, "hard": 2, "good": 3, "easy": 4}
MAX_REQUEUES = 2


class TutorError(Exception):
    """Error with a message meant to be shown to the model as-is."""


@dataclass
class WordInput:
    word: str
    romaji: str
    meaning: str
    kana: str = ""
    type: str = ""
    note: str = ""
    example: str = ""
    example_en: str = ""
    source: str = ""
    production: bool | None = None


@dataclass
class Session:
    queue: deque[dict] = field(default_factory=deque)
    log: list[tuple[int, str]] = field(default_factory=list)
    requeues: dict[int, int] = field(default_factory=dict)
    started: float = field(default_factory=time.time)


def _escape_value(text: str) -> str:
    """Escape user text for use inside a quoted Anki field search."""
    out = text.replace("\\", "\\\\").replace('"', '\\"')
    return out.replace("*", "\\*").replace("_", "\\_")


class TutorStore:
    def __init__(self, collection_path: str, deck_name: str, production_default: bool = False):
        self.path = collection_path
        self.deck_name = deck_name
        self.production_default = production_default
        self.lock = threading.RLock()
        self.col = Collection(collection_path)
        self.session: Session | None = None
        self.on_change: Callable[[], None] = lambda: None

    # ------------------------------------------------------------------ setup
    def ensure_setup(self) -> list[str]:
        """Create the deck and note type if missing. Returns what was created."""
        made: list[str] = []
        with self.lock:
            if self.col.decks.id_for_name(self.deck_name) is None:
                self.col.decks.id(self.deck_name, create=True)
                made.append(f"deck '{self.deck_name}'")
            if self.col.models.by_name(content.NOTETYPE_NAME) is None:
                mm = self.col.models
                nt = mm.new(content.NOTETYPE_NAME)
                for name in content.FIELDS:
                    mm.add_field(nt, mm.new_field(name))
                for tpl in content.TEMPLATES:
                    t = mm.new_template(tpl["name"])
                    t["qfmt"] = tpl["front"]
                    t["afmt"] = tpl["back"]
                    mm.add_template(nt, t)
                nt["css"] = content.CSS
                mm.add(nt)
                made.append(f"note type '{content.NOTETYPE_NAME}'")
        if made:
            self.on_change()
        return made

    def close(self) -> None:
        with self.lock:
            self.col.close()

    # --------------------------------------------------------------- helpers
    def _nt(self) -> dict:
        nt = self.col.models.by_name(content.NOTETYPE_NAME)
        if nt is None:
            self.ensure_setup()
            nt = self.col.models.by_name(content.NOTETYPE_NAME)
        assert nt is not None
        return nt

    def _did(self) -> int:
        return int(self.col.decks.id(self.deck_name, create=True))

    def _q(self, extra: str = "") -> str:
        base = self.col.build_search_string(
            SearchNode(deck=self.deck_name), SearchNode(note=content.NOTETYPE_NAME)
        )
        return f"{base} -is:suspended {extra}".strip()

    def _find(self, extra: str = "", order: str | bool = False) -> list[int]:
        return [int(c) for c in self.col.find_cards(self._q(extra), order=order)]

    def _existing_words(self) -> set[str]:
        mid = self._nt()["id"]
        return {
            flds.split("\x1f", 1)[0].strip()
            for flds in self.col.db.list("select flds from notes where mid = ?", mid)
        }

    def _get_card(self, cid: int) -> Card | None:
        try:
            card = self.col.get_card(cid)  # type: ignore[arg-type]
        except NotFoundError:
            return None
        if card.note().note_type()["name"] != content.NOTETYPE_NAME:  # type: ignore[index]
            return None
        return card

    @staticmethod
    def _status(card: Card) -> str:
        if card.queue < 0:
            return "suspended" if card.queue == -1 else "buried"
        return {0: "new", 1: "learning", 2: "review", 3: "relearning"}.get(card.type, "?")

    def _next_due_text(self, card: Card) -> str:
        if card.queue == 1:  # intraday learning: due is an epoch timestamp
            mins = max(1, math.ceil((card.due - time.time()) / 60))
            return f"~{mins}m" if mins < 60 else f"~{round(mins / 60)}h"
        if card.queue in (2, 3):
            days = card.due - self.col.sched.today
            return "today" if days <= 0 else f"{days}d"
        return "new"

    def _view(self, card: Card) -> dict:
        n = card.note()
        ex = n["Example"]
        return {
            "card_id": card.id,
            "direction": "recognition" if card.ord == 0 else "production",
            "status": self._status(card),
            "word": n["Word"],
            "kana": n["Kana"],
            "romaji": n["Romaji"],
            "meaning": n["Meaning"],
            "type": n["Type"],
            "note": n["Note"],
            "example": to_plain(ex) if ex else "",
            "example_kana": to_kana(ex) if ex and has_ruby(ex) else "",
            "example_en": n["ExampleEnglish"],
            "source": n["Source"],
            "lapses": card.lapses,
        }

    @staticmethod
    def _fmt(v: dict, with_answer: bool = True) -> str:
        jp = f"{v['kana']} ({v['word']})" if v["kana"] else v["word"]
        head = f"[card {v['card_id']} · {v['direction']} · {v['status']}"
        head += f" · missed {v['lapses']}x]" if v["lapses"] else "]"
        if v["direction"] == "recognition":
            lines = [head, f"ask: {jp}"]
            if with_answer:
                lines.append(f"ans: {v['romaji']} = {v['meaning']}")
        else:
            lines = [head, f"ask (say it in Japanese): {v['meaning']}"]
            if with_answer:
                lines.append(f"ans: {jp} · {v['romaji']}")
        if v["note"]:
            lines.append(f"tip: {v['note']}")
        if v["example"]:
            ex = v["example"]
            if v["example_kana"]:
                ex += f" ({v['example_kana']})"
            if v["example_en"]:
                ex += f" = {v['example_en']}"
            lines.append(f"ex: {ex}")
        if v["source"]:
            lines.append(f"from: {v['source']}")
        return "\n".join(lines)

    def _short(self, v: dict) -> str:
        jp = f"{v['word']} ({v['kana']})" if v["kana"] else v["word"]
        return f"{jp} {v['romaji']} = {v['meaning']}"

    # ------------------------------------------------------------ weak cards
    def _weak(self, limit: int, min_score: float) -> list[tuple[Card, float, str]]:
        cids = self._find("-is:new")
        if not cids:
            return []
        rows = self.col.db.all(
            f"select cid, id, ease from revlog where cid in {ids2str(cids)} and ease > 0 order by id"
        )
        hist: dict[int, list[tuple[int, int]]] = {}
        for cid, rid, ease in rows:
            hist.setdefault(cid, []).append((rid, ease))
        recent_cut = (time.time() - 14 * 86400) * 1000
        scored: list[tuple[Card, float, str]] = []
        for cid in cids:
            card = self.col.get_card(cid)  # type: ignore[arg-type]
            h = hist.get(cid, [])
            total = len(h)
            again_total = sum(1 for _, e in h if e == 1)
            recent_again = sum(1 for rid, e in h if e == 1 and rid >= recent_cut)
            streak = 0
            for _, e in reversed(h):
                if e == 1:
                    break
                streak += 1
            last_miss = bool(h) and h[-1][1] == 1
            low_ease = 0 < card.factor < 2300
            score = (
                3 * recent_again
                + 2 * card.lapses
                + (4 * again_total / total if total else 0)
                + (3 if last_miss else 0)
                + (1.5 if low_ease else 0)
                - 0.5 * min(streak, 4)
            )
            if score < min_score:
                continue
            why = []
            if recent_again:
                why.append(f"missed {recent_again}x in last 2 wks")
            if card.lapses:
                why.append(f"forgotten {card.lapses}x after learning")
            if last_miss:
                why.append("missed last time")
            if total:
                why.append(f"{round(100 * (total - again_total) / total)}% right overall")
            scored.append((card, round(score, 1), ", ".join(why)))
        scored.sort(key=lambda t: t[1], reverse=True)
        return scored[:limit]

    def weak_words(self, limit: int = 5) -> str:
        with self.lock:
            weak = self._weak(limit, min_score=1.0)
            if not weak:
                return "No weak words right now. Nothing has been missed recently."
            out = []
            for card, score, why in weak:
                v = self._view(card)
                out.append(f"- card {v['card_id']} ({v['direction']}): {self._short(v)} · {why}")
            return "Weakest first:\n" + "\n".join(out)

    # --------------------------------------------------------------- session
    # Each operation has a *_data() form (structured, used by the web app) and a text
    # form (compact, used by the MCP tools so the model reads as few tokens as possible).

    def start_session_data(self, new_words: int = 3, review_limit: int = 12,
                           auto_unlock: bool = True) -> dict:
        with self.lock:
            self.ensure_setup()
            s = Session()
            review: list[int] = [c.id for c, _, _ in self._weak(3, min_score=3.0)]
            for cid in self._find("is:due", order="c.type desc, c.due asc"):
                if cid not in review:
                    review.append(cid)
            review = review[:review_limit]
            s.queue.extend({"kind": "quiz", "cid": cid} for cid in review)

            unlocked: int | None = None
            new_cids = self._new_cards(new_words)
            if not new_cids and not review and auto_unlock:
                added, batches = self._unlock(1)
                if added:
                    unlocked = batches[0]
                    new_cids = self._new_cards(new_words)
            self._enqueue_new(s, new_cids, front=False)
            self.session = s
            return {"stats": self._stats(), "unlocked": unlocked, "item": self._pop_next()}

    def start_session(self, new_words: int = 3, review_limit: int = 12, auto_unlock: bool = True) -> str:
        d = self.start_session_data(new_words, review_limit, auto_unlock)
        unlocked = (f"(no reviews or new cards were waiting, so batch {d['unlocked']} was unlocked)\n"
                    if d["unlocked"] else "")
        return (f"{content.TUTOR_RULES}\nSTATS: {self._stats_text(d['stats'])}\n{unlocked}\n"
                f"{self._fmt_item(d['item'])}")

    def _new_cards(self, max_words: int) -> list[int]:
        if max_words <= 0:
            return []
        cids = self._find("is:new", order="c.due asc, c.ord asc")
        notes: list[int] = []
        picked: list[int] = []
        for cid in cids:
            nid = self.col.get_card(cid).nid  # type: ignore[arg-type]
            if nid not in notes:
                if len(notes) >= max_words:
                    continue
                notes.append(nid)
            picked.append(cid)
        return picked

    def _enqueue_new(self, s: Session, cids: list[int], front: bool) -> None:
        if not cids:
            return
        rec = [c for c in cids if self.col.get_card(c).ord == 0]  # type: ignore[arg-type]
        prod = [c for c in cids if c not in rec]
        items: list[dict] = []
        for i in range(0, len(rec), content.BATCH_SIZE):
            chunk = rec[i : i + content.BATCH_SIZE]
            items.append({"kind": "intro", "cids": chunk})
            items.extend({"kind": "quiz", "cid": c} for c in chunk)
        items.extend({"kind": "quiz", "cid": c} for c in prod)
        if front:
            s.queue.extendleft(reversed(items))
        else:
            s.queue.extend(items)

    def _pop_next(self) -> dict:
        """Next queue item: {kind: intro, cards, first} | {kind: quiz, card, left} | {kind: empty}."""
        s = self.session
        if s is None:
            return {"kind": "none"}
        while s.queue:
            item = s.queue.popleft()
            if item["kind"] == "intro":
                cards = [c for c in (self._get_card(cid) for cid in item["cids"]) if c]
                if not cards:
                    continue
                return {"kind": "intro", "cards": [self._view(c) for c in cards], "first": self._pop_next()}
            card = self._get_card(item["cid"])
            if card is None or card.queue < 0:
                continue
            left = sum(1 for i in s.queue if i["kind"] == "quiz")
            return {"kind": "quiz", "card": self._view(card), "left": left}
        return {"kind": "empty"}

    def _fmt_item(self, item: dict) -> str:
        kind = item["kind"]
        if kind == "intro":
            lines = [f"- {self._short(v)}" + (f" · tip: {v['note']}" if v["note"] else "") for v in item["cards"]]
            plural = "word" if len(item["cards"]) == 1 else f"{len(item['cards'])} words"
            return (f"INTRO: teach these new {plural} in one short message, then ask the first quiz below.\n"
                    + "\n".join(lines) + f"\n\nFIRST QUIZ:\n{self._fmt_item(item['first'])}")
        if kind == "quiz":
            return f"NEXT ({item['left']} more after this):\n{self._fmt(item['card'])}"
        if kind == "none":
            return "No session. Call jp_start_session."
        return "QUEUE EMPTY. Offer more with jp_next_batch (3 new words), or wrap up with jp_end_session."

    def peek_card(self, card_id: int) -> dict:
        with self.lock:
            card = self._get_card(card_id)
            if card is None:
                raise TutorError(f"Card {card_id} isn't a tutor card.")
            return self._view(card)

    def raw_example(self, card_id: int) -> str:
        """Example sentence in Anki furigana syntax (the app renders it as <ruby>)."""
        with self.lock:
            c = self._get_card(card_id)
            return c.note()["Example"] if c else ""

    def answer_data(self, card_id: int, result: str) -> dict:
        with self.lock:
            card = self._get_card(card_id)
            if card is None:
                raise TutorError(f"Card {card_id} isn't a tutor card. Use the card_id from the last NEXT item.")
            v = self._view(card)
            card.start_timer()
            self.col.sched.answerCard(card, EASE[result])
            card = self.col.get_card(card.id)
            if self.session is None:
                self.session = Session()
            s = self.session
            s.log.append((card.id, result))
            requeued = False
            if result == "again":
                self.col.tags.bulk_add([card.nid], content.TAG_MISSED)
                if s.requeues.get(card.id, 0) < MAX_REQUEUES:
                    s.requeues[card.id] = s.requeues.get(card.id, 0) + 1
                    s.queue.insert(min(2, len(s.queue)), {"kind": "quiz", "cid": card.id})
                    requeued = True
            # Right answers in a row this session (for the app's hype lines).
            streak = 0
            for _, r in reversed(s.log):
                if r == "again":
                    break
                streak += 1
            saved = {"word": v["word"], "result": result, "next_review": self._next_due_text(card),
                     "requeued": requeued, "in_a_row": streak}
            nxt = self._pop_next()
        self.on_change()
        return {"saved": saved, "next": nxt}

    def answer(self, card_id: int, result: str) -> str:
        d = self.answer_data(card_id, result)
        sv = d["saved"]
        extra = " · re-asking soon" if sv["requeued"] else ""
        return (f"saved: {sv['word']} → {sv['result']} (next review {sv['next_review']}){extra}\n"
                f"{self._fmt_item(d['next'])}")

    def next_batch_data(self, batches: int = 1) -> dict:
        with self.lock:
            added, nums = self._unlock(batches)
            if not added:
                return {"added": [], "batches": [], "item": None}
            if self.session is None:
                self.session = Session()
            self._enqueue_new(self.session, self._cids_for_words(added), front=True)
            item = self._pop_next()
        self.on_change()
        return {"added": added, "batches": nums, "item": item}

    def next_batch(self, batches: int = 1) -> str:
        d = self.next_batch_data(batches)
        if not d["added"]:
            return "All starter batches are unlocked. Add words from shows/convos with jp_add_words."
        return (f"Unlocked batch {', '.join(map(str, d['batches']))}: {', '.join(d['added'])}\n"
                f"{self._fmt_item(d['item'])}")

    def _unlock(self, batches: int) -> tuple[list[str], list[int]]:
        existing = self._existing_words()
        added: list[str] = []
        nums: list[int] = []
        for i, batch in enumerate(content.CURRICULUM, start=1):
            if len(nums) >= batches:
                break
            missing = [w for w in batch if w["word"] not in existing]
            if not missing:
                continue
            for w in missing:
                self._add(WordInput(
                    word=w["word"], kana=w["kana"], romaji=w["romaji"], meaning=w["meaning"],
                    type=w["type"], note=w["note"], example=w["example"], example_en=w["example_en"],
                ), tags=[content.TAG_ROOT, content.batch_tag(i)])
                added.append(w["word"])
            nums.append(i)
        return added, nums

    def _cids_for_words(self, words: list[str]) -> list[int]:
        cids: list[int] = []
        for w in words:
            q = self._q(f'"Word:{_escape_value(w)}"')
            cids.extend(int(c) for c in self.col.find_cards(q, order="c.ord asc"))
        return cids

    def _add(self, w: WordInput, tags: list[str]) -> int | None:
        nt = self._nt()
        note = self.col.new_note(nt)
        kana = w.kana.strip()
        if kana == w.word.strip() or is_all_kana(w.word):
            kana = ""
        note["Word"] = w.word.strip()
        note["Kana"] = kana
        note["Romaji"] = w.romaji.strip()
        note["Meaning"] = w.meaning.strip()
        note["Type"] = w.type.strip()
        note["Note"] = w.note.strip()
        note["Example"] = w.example.strip()
        note["ExampleEnglish"] = w.example_en.strip()
        note["Source"] = w.source.strip()
        prod = self.production_default if w.production is None else w.production
        note["Production"] = "y" if prod else ""
        note.tags = tags
        if note.fields_check() == 2:  # DUPLICATE
            return None
        self.col.add_note(note, self._did())  # type: ignore[arg-type]
        return note.id

    def add_words_data(self, words: list[WordInput], quiz_now: bool = True) -> dict:
        with self.lock:
            existing = self._existing_words()
            added: list[str] = []
            skipped: list[str] = []
            for w in words:
                if not w.word.strip() or not w.meaning.strip():
                    skipped.append(f"{w.word or '?'} (missing word or meaning)")
                    continue
                if w.word.strip() in existing:
                    skipped.append(f"{w.word} (already in deck)")
                    continue
                tags = [content.TAG_ROOT] + ([content.TAG_MINED] if w.source.strip() else [])
                if self._add(w, tags) is None:
                    skipped.append(f"{w.word} (duplicate)")
                    continue
                existing.add(w.word.strip())
                added.append(w.word.strip())
            item = None
            if added and quiz_now:
                if self.session is None:
                    self.session = Session()
                self._enqueue_new(self.session, self._cids_for_words(added), front=True)
                item = self._pop_next()
        if added:
            self.on_change()
        return {"added": added, "skipped": skipped, "item": item}

    def add_words(self, words: list[WordInput], quiz_now: bool = True) -> str:
        d = self.add_words_data(words, quiz_now)
        msg = f"added: {', '.join(d['added']) or 'none'}"
        if d["skipped"]:
            msg += f"\nskipped: {'; '.join(d['skipped'])}"
        if d["item"]:
            msg += "\n" + self._fmt_item(d["item"])
        return msg

    def find_words(self, query: str, limit: int = 10) -> str:
        with self.lock:
            v = _escape_value(query.strip())
            q = " OR ".join(f'"{f}:*{v}*"' for f in ("Word", "Kana", "Romaji", "Meaning", "Source"))
            cids = self._find(f"({q}) card:1")
            if not cids:
                return f"No words matching '{query}'."
            lines = []
            for cid in cids[:limit]:
                card = self.col.get_card(cid)  # type: ignore[arg-type]
                vw = self._view(card)
                due = self._next_due_text(card)
                lines.append(f"- card {cid}: {self._short(vw)} · {vw['status']}, next {due}"
                             + (f", missed {vw['lapses']}x" if vw["lapses"] else ""))
            more = f"\n(+{len(cids) - limit} more)" if len(cids) > limit else ""
            return "\n".join(lines) + more

    # ----------------------------------------------------------------- stats
    def _streak(self) -> int:
        cutoff = self.col.sched.day_cutoff
        streak = 0
        for i in range(0, 366):
            start, end = (cutoff - 86400 * (i + 1)) * 1000, (cutoff - 86400 * i) * 1000
            n = self.col.db.scalar("select count() from revlog where id >= ? and id < ?", start, end)
            if n:
                streak += 1
            elif i > 0:
                break
        return streak

    def _stats(self) -> dict:
        return {
            "words": len(self._existing_words()),
            "not_started": len(self._find("is:new card:1")),
            "due": len(self._find("is:due")),
            "today": self.col.db.scalar(
                "select count() from revlog where id > ?", (self.col.sched.day_cutoff - 86400) * 1000),
            "streak": self._streak(),
        }

    @staticmethod
    def _stats_text(st: dict) -> str:
        return (f"{st['words']} words ({st['not_started']} not started) · {st['due']} due · "
                f"{st['today']} answers today · streak {st['streak']}d")

    def progress_data(self) -> dict:
        with self.lock:
            existing = self._existing_words()
            d = self._stats()
            d.update({
                "learning": len(self._find("(is:learn) card:1")),
                "in_review": len(self._find("is:review -is:learn card:1")),
                "solid": len(self._find("prop:ivl>=21 card:1")),
                "batches_unlocked": sum(1 for b in content.CURRICULUM if all(w["word"] in existing for w in b)),
                "batches_total": len(content.CURRICULUM),
                "mined": len(self.col.find_notes(self._q(f"tag:{content.TAG_MINED}"))),
                "shakiest": [self._view(c)["word"] for c, _, _ in self._weak(3, min_score=3.0)],
            })
            return d

    def progress(self) -> str:
        d = self.progress_data()
        lines = [
            self._stats_text(d),
            f"learning {d['learning']} · in review {d['in_review']} · solid (21d+) {d['solid']}",
            f"starter batches: {d['batches_unlocked']}/{d['batches_total']} · words mined from shows: {d['mined']}",
        ]
        if d["shakiest"]:
            lines.append("shakiest: " + ", ".join(d["shakiest"]))
        return "\n".join(lines)

    def end_session_data(self) -> dict:
        with self.lock:
            s = self.session
            self.session = None
            if s is None or not s.log:
                return {"cards": 0, "answers": 0, "right_first_try": 0, "minutes": 0, "missed": []}
            first_try: dict[int, str] = {}
            for cid, r in s.log:
                first_try.setdefault(cid, r)
            missed = []
            for cid, r in first_try.items():
                c = self._get_card(cid) if r == "again" else None
                if c:
                    missed.append(self._view(c)["word"])
            return {
                "cards": len(first_try),
                "answers": len(s.log),
                "right_first_try": sum(1 for r in first_try.values() if r != "again"),
                "minutes": max(1, round((time.time() - s.started) / 60)),
                "missed": missed,
            }

    def end_session(self) -> str:
        d = self.end_session_data()
        if not d["answers"]:
            return "No answers this session."
        out = (f"session: {d['cards']} cards, {d['answers']} answers, "
               f"{d['right_first_try']}/{d['cards']} right first try, ~{d['minutes']} min")
        if d["missed"]:
            out += f"\nmissed: {', '.join(d['missed'])} (they'll come back first next time)"
        return out
