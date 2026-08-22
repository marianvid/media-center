from __future__ import annotations

import threading
import time
from pathlib import Path

from .db import Database
from .jellyfin import JellyfinClient
from .scanner import CatalogScanner


class AdminIndexer:
    def __init__(self, db: Database, scanner: CatalogScanner, jellyfin: JellyfinClient, readonly_root: Path, jellyfin_database: Path):
        self.db = db
        self.scanner = scanner
        self.jellyfin = jellyfin
        self.readonly_root = readonly_root
        self.jellyfin_database = jellyfin_database
        self._guard = threading.Lock()
        self._paths: set[str] = set()
        self._full = False
        self._running = False

    def schedule(self, *admin_paths: str, full: bool = False) -> bool:
        with self._guard:
            self._paths.update(path for path in admin_paths if path)
            self._full = self._full or full
            if self._running:
                return False
            self._running = True
        threading.Thread(target=self._run, daemon=True, name="admin-index-refresh").start()
        return True

    def _job(self, state: str, message: str) -> None:
        with self.db.connect() as db:
            db.execute(
                "INSERT INTO jobs(name,state,message,updated_at) VALUES('admin_refresh',?,?,CURRENT_TIMESTAMP) "
                "ON CONFLICT(name) DO UPDATE SET state=excluded.state,message=excluded.message,updated_at=CURRENT_TIMESTAMP",
                (state, message),
            )

    def _run(self) -> None:
        try:
            while True:
                with self._guard:
                    paths, full = sorted(self._paths), self._full
                    self._paths.clear()
                    self._full = False
                self._job("running", "Updating Media Center and Jellyfin indexes")
                video_paths = [path.removeprefix("video/") for path in paths if path == "video" or path.startswith("video/")]
                if full:
                    self.jellyfin.refresh_library()
                elif video_paths:
                    self.jellyfin.refresh_paths(video_paths, self.readonly_root / "video", self.jellyfin_database)
                self.scanner.run_blocking()
                if full or video_paths:
                    # Jellyfin refreshes asynchronously. Reconcile IDs more than once so
                    # additions and larger folders become playable without manual work.
                    for delay in (3, 12, 30):
                        time.sleep(delay)
                        self.jellyfin.sync_catalog(self.db, self.readonly_root / "video", self.jellyfin_database)
                        with self._guard:
                            if self._paths or self._full:
                                break
                with self._guard:
                    if not self._paths and not self._full:
                        self._running = False
                        break
            self._job("complete", "Media Center and Jellyfin indexes are up to date")
        except Exception as exc:
            with self._guard:
                self._running = False
            self._job("error", str(exc))
