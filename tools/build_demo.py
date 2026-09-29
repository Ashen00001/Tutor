"""Build a self-contained preview of the web app (sample words, in-browser stand-in backend).

    python tools/build_demo.py out.html

Used to share the UI for feedback without deploying the server. The real app is
src/anki_jp_tutor/web/index.html served by the tutor server.
"""

from __future__ import annotations

import base64
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from anki_jp_tutor.content import CURRICULUM  # noqa: E402
from anki_jp_tutor.furigana import is_all_kana, to_ruby_html  # noqa: E402

WEB = ROOT / "src" / "anki_jp_tutor" / "web"

# The preview frame already pads for the phone's safe areas and sizes the page;
# the self-hosted app does that itself, so undo it here.
OVERRIDES = """
:root { color-scheme: dark; }
html, body { height: 100%; }
#app { height: 100%; padding-top: 6px; padding-bottom: 12px; }
.sheet { padding-bottom: 16px; }
"""


def curriculum_json() -> str:
    batches = []
    for batch in CURRICULUM[:6]:
        out = []
        for w in batch:
            kana = "" if (w["kana"] == w["word"] or is_all_kana(w["word"])) else w["kana"]
            out.append({
                "word": w["word"], "kana": kana, "romaji": w["romaji"], "meaning": w["meaning"],
                "type": w["type"], "note": w["note"], "example_en": w["example_en"],
                "example_html": to_ruby_html(w["example"]) if w["example"] else "",
            })
        batches.append(out)
    return json.dumps(batches, ensure_ascii=False)


def main(out_path: str) -> None:
    html = (WEB / "index.html").read_text()
    title = re.search(r"<title>.*?</title>", html, re.S).group(0)
    style = re.search(r"<style>(.*?)</style>", html, re.S).group(1)
    body = re.search(r"<body>(.*?)</body>", html, re.S).group(1)
    mock = (ROOT / "tools" / "demo_api.js").read_text().replace("__CURRICULUM__", curriculum_json())
    icon = "data:image/svg+xml;base64," + base64.b64encode((WEB / "icon.svg").read_bytes()).decode()
    page = (f"{title}\n<link rel=\"icon\" href=\"{icon}\">\n<style>{style}{OVERRIDES}</style>\n"
            f"<script>{mock}</script>\n{body}")
    Path(out_path).write_text(page)
    print(f"wrote {out_path} ({len(page) // 1024} KB)")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "demo.html")
