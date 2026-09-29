"""Helpers for Anki's furigana syntax: ' 漢字[かんじ]' (a space marks where the ruby base starts)."""

from __future__ import annotations

import re

_RUBY = re.compile(r"(?:^| )([^ \[\]]+)\[([^\]]*)\]")
_KANA = re.compile(r"^[぀-ヿーｦ-ﾟ\s、。！？!?～〜ー…・「」]+$")


def to_plain(text: str) -> str:
    """' 水[みず] 飲[の]みたい。' -> '水飲みたい。'"""
    return _RUBY.sub(lambda m: m.group(1), text).strip()


def to_kana(text: str) -> str:
    """' 水[みず] 飲[の]みたい。' -> 'みずのみたい。' (each ruby base replaced by its reading)."""
    def repl(m: re.Match[str]) -> str:
        base, reading = m.group(1), m.group(2)
        return reading or base

    return _RUBY.sub(repl, text).strip()


def to_ruby_html(text: str) -> str:
    """' 水[みず] 飲[の]みたい。' -> '<ruby>水<rt>みず</rt></ruby><ruby>飲<rt>の</rt></ruby>みたい。' (escaped)."""
    import html

    out, pos = [], 0
    for m in _RUBY.finditer(text):
        out.append(html.escape(text[pos : m.start()]))
        base, reading = m.group(1), m.group(2)
        out.append(f"<ruby>{html.escape(base)}<rt>{html.escape(reading)}</rt></ruby>" if reading else html.escape(base))
        pos = m.end()
    out.append(html.escape(text[pos:]))
    return "".join(out).strip()


def is_all_kana(text: str) -> bool:
    return bool(_KANA.match(text))


def has_ruby(text: str) -> bool:
    return bool(_RUBY.search(text))
