"""Entry point.

  python -m anki_jp_tutor                 # HTTP server (custom connector)
  python -m anki_jp_tutor --stdio         # local stdio (Claude Desktop config / testing)
  python -m anki_jp_tutor login           # one-time AnkiWeb login (stores a key, not your password)
"""

from __future__ import annotations

import argparse
import atexit
import getpass
import logging
import os
import secrets
import sys
from pathlib import Path

from .chat import ChatClient
from .server import build_server
from .tts import ElevenLabsTTS
from .store import TutorStore
from .sync import SyncManager

log = logging.getLogger("anki_jp_tutor")


def _env_bool(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _secret(data: Path) -> str:
    s = os.environ.get("JP_TUTOR_SECRET", "").strip()
    if s:
        return s
    f = data / "secret.txt"
    if not f.exists():
        f.write_text(secrets.token_urlsafe(24))
        os.chmod(f, 0o600)
    return f.read_text().strip()


def main() -> None:
    ap = argparse.ArgumentParser(prog="anki_jp_tutor")
    ap.add_argument("command", nargs="?", default="serve", choices=["serve", "login", "url", "app-url"])
    ap.add_argument("--stdio", action="store_true", help="serve over stdio instead of HTTP")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="%(asctime)s %(name)s %(message)s")

    data = Path(os.environ.get("JP_TUTOR_DATA", "./data")).resolve()
    data.mkdir(parents=True, exist_ok=True)
    secret = _secret(data)
    public = os.environ.get("PUBLIC_URL", "").rstrip("/")
    path = f"/mcp/{secret}"

    if args.command == "url":
        print(f"{public}{path}")  # just the path when PUBLIC_URL isn't set
        return
    if args.command == "app-url":
        print(f"{public}/app/{secret}/")
        return

    store = TutorStore(
        str(data / "collection.anki2"),
        deck_name=os.environ.get("JP_TUTOR_DECK", "Japanese Tutor"),
        production_default=_env_bool("JP_TUTOR_PRODUCTION_CARDS"),
    )
    sync = SyncManager(
        store,
        data,
        endpoint=os.environ.get("JP_TUTOR_SYNC_ENDPOINT") or None,
        conflict=os.environ.get("JP_TUTOR_FULL_SYNC_CONFLICT", "download"),
    )
    store.on_change = sync.schedule

    if args.command == "login":
        user = os.environ.get("ANKIWEB_USERNAME") or input("AnkiWeb email: ")
        pw = os.environ.get("ANKIWEB_PASSWORD") or getpass.getpass("AnkiWeb password: ")
        sync.login(user, pw)
        print("First sync:", sync.sync_now())
        store.ensure_setup()
        print("Setup sync:", sync.sync_now())
        store.close()
        return

    # Optional first-boot login from env (remove the password from env afterwards).
    if not sync.enabled and os.environ.get("ANKIWEB_USERNAME") and os.environ.get("ANKIWEB_PASSWORD"):
        try:
            sync.login(os.environ["ANKIWEB_USERNAME"], os.environ["ANKIWEB_PASSWORD"])
        except Exception as e:  # noqa: BLE001
            log.error("AnkiWeb login failed (continuing without sync): %s", e)

    if sync.enabled:
        log.info("startup sync: %s", sync.sync_now())
    if store.ensure_setup() and sync.enabled:
        log.info("setup sync: %s", sync.sync_now())

    def shutdown() -> None:
        try:
            if sync.enabled:
                sync.sync_now()
        finally:
            store.close()

    atexit.register(shutdown)

    if args.stdio:
        build_server(store, sync).run("stdio")
        return

    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "8000"))
    chat = ChatClient.from_env(store)
    tts = ElevenLabsTTS.from_env(data)
    origin = public or f"http://{host}:{port}"
    log.info("Connector URL (Claude): %s%s", origin, path)
    log.info("App URL (phone/browser): %s/app/%s/", origin, secret)
    log.info("%s · conversation: %s · voice: %s", sync.status(), "on" if chat else "off (set ANTHROPIC_API_KEY)",
             f"ElevenLabs {tts.model}" if tts else "browser voices (set ELEVENLABS_API_KEY)")
    build_server(store, sync, host=host, port=port, path=path, secret=secret, chat=chat, tts=tts).run("streamable-http")


if __name__ == "__main__":
    main()
