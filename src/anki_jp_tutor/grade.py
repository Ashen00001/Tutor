"""Instant answer checking + the tutor's spoken lines (no model call, so the app replies instantly).

recognition card: learner gives the English meaning (typed or spoken).
production card: learner gives the Japanese (kana/kanji from speech, or typed kana/romaji).
"""

from __future__ import annotations

import random
import re
import unicodedata
from difflib import SequenceMatcher

Verdict = str  # "correct" | "close" | "wrong"

_STOP = {"to", "a", "an", "the", "be", "is", "are", "am", "it", "its", "im", "i", "my", "of", "for", "you", "your"}
_ALIASES = {"ok": "okay", "k": "okay", "yup": "yeah", "yep": "yeah", "ya": "yeah", "nope": "nah",
            "thx": "thanks", "cya": "see ya", "u": "you", "rn": "now"}
_PAREN = re.compile(r"\(([^)]*)\)")
_SPLIT = re.compile(r"\s*(?:/|,|;|\bor\b)\s*")
_JA_PUNCT = re.compile(r"[\s、。！？!?.,…・「」『』（）()～〜ー\-]")


# ------------------------------------------------------------------ english
def _stem(t: str) -> str:
    if len(t) > 5 and t.endswith("ing"):
        return t[:-3]
    if t.endswith(("ches", "shes", "xes")):
        return t[:-2]
    if len(t) > 3 and t.endswith("s") and not t.endswith("ss"):
        return t[:-1]
    if len(t) > 4 and t.endswith("ed"):
        return t[:-2]
    return t


def _en_tokens(text: str) -> list[str]:
    text = text.lower().replace("’", "'").replace("'", "")
    raw = re.findall(r"[a-z]+", text)
    toks = [_ALIASES.get(t, t) for t in raw]
    toks = " ".join(toks).split()
    kept = [t for t in toks if t not in _STOP]
    return [_stem(t) for t in (kept or toks)]


def _en_candidates(meaning: str) -> tuple[list[list[str]], list[list[str]]]:
    """(main candidates, parenthetical 'close-only' candidates) as token lists."""
    aux_text = " ".join(_PAREN.findall(meaning))
    main_text = _PAREN.sub(" ", meaning)
    main = [_en_tokens(p) for p in _SPLIT.split(main_text) if p.strip()]
    aux = [_en_tokens(p) for p in _SPLIT.split(aux_text) if p.strip()]
    return [m for m in main if m], [a for a in aux if a]


def _match_tokens(user: list[str], cand: list[str]) -> Verdict:
    if not user:
        return "wrong"
    u, c = set(user), set(cand)
    if u == c:
        return "correct"
    if c <= u and len(u - c) <= 2:  # said a bit extra ("to eat food")
        return "correct"
    ratio = SequenceMatcher(None, " ".join(user), " ".join(cand)).ratio()
    if ratio >= 0.86:
        return "correct"
    if u <= c and len(u) * 2 >= len(c):  # got the main part ("rice" for "cooked rice")
        return "close"
    if ratio >= (0.8 if len(" ".join(cand)) <= 5 else 0.72):
        return "close"
    return "wrong"


def check_english(text: str, meaning: str) -> Verdict:
    user = _en_tokens(text)
    main, aux = _en_candidates(meaning)
    best = "wrong"
    for cand in main:
        v = _match_tokens(user, cand)
        if v == "correct":
            return "correct"
        if v == "close":
            best = "close"
    if best == "wrong" and any(_match_tokens(user, a) == "correct" for a in aux):
        best = "close"
    return best


# ----------------------------------------------------------------- japanese
def _hira(s: str) -> str:
    s = unicodedata.normalize("NFKC", s)
    return "".join(chr(ord(ch) - 0x60) if "ァ" <= ch <= "ヶ" else ch for ch in s)


def _ja_norm(s: str) -> str:
    return _JA_PUNCT.sub("", _hira(s))


_ROMAJI_FIX = [
    ("sy", "sh"), ("si", "shi"), ("zy", "j"), ("jy", "j"), ("zi", "ji"), ("di", "ji"),
    ("ty", "ch"), ("ti", "chi"), ("tu", "tsu"), ("hu", "fu"), ("cy", "ch"),
    ("ou", "o"), ("oo", "o"), ("uu", "u"), ("aa", "a"), ("ii", "i"), ("ee", "e"), ("ei", "e"),
]


def _romaji_norm(s: str) -> str:
    s = "".join(ch for ch in unicodedata.normalize("NFKD", s) if not unicodedata.combining(ch))
    s = re.sub(r"[^a-z]", "", s.lower())
    s = re.sub(r"m(?=[bp])", "n", s)
    for a, b in _ROMAJI_FIX:
        s = s.replace(a, b)
    s = s.replace("shhi", "shi").replace("chhi", "chi")
    return s


def check_japanese(text: str, word: str, kana: str, romaji: str) -> Verdict:
    if re.search(r"[a-zA-Z]", text) and not re.search(r"[぀-ヿ一-鿿]", text):
        u, r = _romaji_norm(text), _romaji_norm(romaji)
        if not u:
            return "wrong"
        if u == r:
            return "correct"
        if r and r in u and len(u) - len(r) <= 3:  # "maji de"
            return "correct"
        ratio = SequenceMatcher(None, u, r).ratio()
        return "close" if ratio >= 0.65 else "wrong"
    u = _ja_norm(text)
    if not u:
        return "wrong"
    targets = [t for t in {_ja_norm(word), _ja_norm(kana)} if t]
    for t in targets:
        if u == t:
            return "correct"
        if t in u and len(u) - len(t) <= 3:  # speech adds a particle / です
            return "correct"
    ratio = max(SequenceMatcher(None, u, t).ratio() for t in targets) if targets else 0
    return "close" if ratio >= 0.6 else "wrong"


# ------------------------------------------------------------------ replies
_RIGHT = ["Yep!", "Boom.", "Nailed it.", "There it go.", "Clean.", "Easy.", "Yesss.", "Let's go.", "Perfect."]
_HYPE = {3: "Three in a row!", 5: "Five straight, you're cooking.", 8: "Eight in a row. Locked in.",
         12: "Twelve straight. Actually cracked."}
_CLOSE = ["Close enough, I'll count it.", "Basically, yeah.", "Pretty much!"]
_WRONG = ["Ahh, not quite.", "Close, but nah.", "Not that one.", "Nope, tricky one."]
_DUNNO = ["All good.", "No stress.", "That's what practice is for."]


def seg(text: str, lang: str = "en") -> dict:
    return {"t": text, "lang": lang}


def jp_of(v: dict) -> str:
    return v["kana"] or v["word"]


def question_speech(v: dict) -> list[dict]:
    if v["direction"] == "recognition":
        return [seg(jp_of(v), "ja")]
    return [seg("How do you say"), seg(v["meaning"].split("/")[0].strip() + "?")]


def intro_speech(cards: list[dict]) -> list[dict]:
    out = [seg("New words.")]
    for v in cards:
        out += [seg(jp_of(v), "ja"), seg(v["meaning"].split("/")[0].strip() + ".")]
    return out


def check(v: dict, text: str, *, hinted: bool = False, gave_up: bool = False) -> dict:
    """Grade an answer. Nothing is saved here; the app commits with /answer."""
    if gave_up or not text.strip():
        verdict = "wrong"
    elif v["direction"] == "recognition":
        verdict = check_english(text, v["meaning"])
        # Answered with the reading/romaji instead of the meaning? Nudge instead of failing.
        if verdict == "wrong" and check_japanese(text, v["word"], v["kana"], v["romaji"]) == "correct":
            return {"verdict": "retry", "result": None, "line": "That's how you say it. Now what does it mean?",
                    "speech": [seg("That's how you say it. What does it mean?")]}
    else:
        verdict = check_japanese(text, v["word"], v["kana"], v["romaji"])

    result = {"correct": "hard" if hinted else "good", "close": "hard", "wrong": "again"}[verdict]
    jp = jp_of(v)
    if verdict == "correct":
        line = random.choice(_RIGHT)
        speech = [seg(line)]
    elif verdict == "close":
        line = random.choice(_CLOSE)
        speech = [seg(line), seg("It's"), seg(jp, "ja"), seg(v["meaning"].split("/")[0].strip() + ".")]
    else:
        line = random.choice(_DUNNO if gave_up else _WRONG)
        speech = [seg(line), seg("It's"), seg(jp, "ja"), seg(v["meaning"].split("/")[0].strip() + ".")]
    return {"verdict": verdict, "result": result, "line": line, "speech": speech}


def hype(in_a_row: int) -> str | None:
    return _HYPE.get(in_a_row)
