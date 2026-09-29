"""Optional AnkiWeb (or self-hosted sync server) sync for the tutor's collection.

Strategy
- AnkiWeb is the source of truth (it's shared with the learner's phone/desktop Anki apps).
- Pull at session start; push ~20 s after the last change (debounced); push on session end.
- Full-sync cases: server empty -> upload; this collection fresh -> download;
  true conflict -> FULL_SYNC_CONFLICT env ("download" by default, AnkiWeb wins).
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path

import anki.collection  # noqa: F401  (import order matters in the anki package)
from anki.sync import SyncAuth

from .store import TutorStore

log = logging.getLogger("anki_jp_tutor.sync")

NO_CHANGES, NORMAL_SYNC, FULL_SYNC, FULL_DOWNLOAD, FULL_UPLOAD = range(5)
DEBOUNCE_SECS = 20.0
PULL_MAX_AGE = 120.0


class SyncManager:
    def __init__(self, store: TutorStore, data_dir: Path, endpoint: str | None = None,
                 conflict: str = "download"):
        self.store = store
        self.auth_file = data_dir / "sync_auth.json"
        self.state_file = data_dir / "sync_state.json"
        self.endpoint = endpoint or None
        self.conflict = conflict
        self.auth: SyncAuth | None = None
        self.last_sync = 0.0
        self.last_error = ""
        self._timer: threading.Timer | None = None
        self._timer_lock = threading.Lock()
        self._load_auth()

    # ------------------------------------------------------------------ auth
    @property
    def enabled(self) -> bool:
        return self.auth is not None

    def _load_auth(self) -> None:
        if self.auth_file.exists():
            d = json.loads(self.auth_file.read_text())
            self.auth = SyncAuth(hkey=d["hkey"], endpoint=d.get("endpoint") or self.endpoint or None)

    def _save_auth(self) -> None:
        assert self.auth is not None
        self.auth_file.write_text(json.dumps({"hkey": self.auth.hkey, "endpoint": self.auth.endpoint or ""}))
        os.chmod(self.auth_file, 0o600)

    def login(self, username: str, password: str) -> None:
        with self.store.lock:
            self.auth = self.store.col.sync_login(username, password, self.endpoint)
        self._save_auth()
        log.info("Logged in to sync server; key saved (password not stored).")

    def _synced_before(self) -> bool:
        return self.state_file.exists() and json.loads(self.state_file.read_text()).get("synced_once", False)

    def _mark_synced(self) -> None:
        self.state_file.write_text(json.dumps({"synced_once": True}))

    # ------------------------------------------------------------------ sync
    def sync_now(self) -> str:
        """Blocking sync. Returns a short status string (never raises)."""
        if not self.enabled:
            return "sync off"
        with self._timer_lock:
            if self._timer:
                self._timer.cancel()
                self._timer = None
        try:
            with self.store.lock:
                col = self.store.col
                out = col.sync_collection(self.auth, False)  # type: ignore[arg-type]
                if out.new_endpoint:
                    self.auth = SyncAuth(hkey=self.auth.hkey, endpoint=out.new_endpoint)  # type: ignore[union-attr]
                    self._save_auth()
                req = out.required
                did = "synced"
                if req in (FULL_DOWNLOAD, FULL_UPLOAD, FULL_SYNC):
                    if req == FULL_UPLOAD:
                        upload = True
                    elif req == FULL_DOWNLOAD:
                        upload = False
                    else:
                        upload = self._synced_before() and self.conflict == "upload"
                    col.close_for_full_sync()
                    try:
                        col.full_upload_or_download(auth=self.auth, server_usn=out.server_media_usn, upload=upload)
                    finally:
                        col.reopen(after_full_sync=True)
                    did = "full upload" if upload else "full download"
                    self.store.session = None if not upload else self.store.session
                    # A download may not contain the tutor deck/note type yet.
                    if self.store.ensure_setup():
                        col.sync_collection(self.auth, False)  # type: ignore[arg-type]
                self._mark_synced()
            self.last_sync = time.time()
            self.last_error = ""
            log.info("sync ok: %s", did)
            return did
        except Exception as e:  # noqa: BLE001 - report, never crash a tool call
            self.last_error = str(e)
            log.warning("sync failed: %s", e)
            return f"sync failed: {e}"

    def maybe_pull(self) -> str:
        if self.enabled and time.time() - self.last_sync > PULL_MAX_AGE:
            return self.sync_now()
        return "sync skipped (recent)" if self.enabled else "sync off"

    def schedule(self) -> None:
        """Debounced push after changes."""
        if not self.enabled:
            return
        with self._timer_lock:
            if self._timer:
                self._timer.cancel()
            self._timer = threading.Timer(DEBOUNCE_SECS, self.sync_now)
            self._timer.daemon = True
            self._timer.start()

    def status(self) -> str:
        if not self.enabled:
            return "sync: off (tutor-only deck)"
        ago = f"{round((time.time() - self.last_sync) / 60)}m ago" if self.last_sync else "not yet"
        err = f" · last error: {self.last_error}" if self.last_error else ""
        return f"sync: on · last {ago}{err}"
