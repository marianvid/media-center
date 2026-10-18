"""Refresh one library folder when the user asks: catalog, Jellyfin, thumbnails.

Nothing in Media Center runs on a timer any more: the media HDD may sleep
until the user presses Refresh. A refresh covers the folder on screen and
everything below it; at a library root it covers the whole library.

Design: Template Method. ``FolderRefresher.run`` fixes the order of the
three steps; each step goes through an injected collaborator (catalog
scanner, Jellyfin client, thumbnail maker), so this module holds only the
workflow and is tested without a disk, a database server or Jellyfin.
See docs/decisions/0003-refresh-on-demand.md.
"""
from __future__ import annotations

import threading
import time
from collections.abc import Callable, Sequence
from pathlib import Path

from .db import Database
from .jellyfin import JellyfinError
from .playback import JELLYFIN_LIBRARIES

JOB = "folder_refresh"
LIBRARIES = ("amintiri", "audio", "video")
PREVIEW_TYPES = ("image", "video")


class FolderRefresher:
    def __init__(
        self,
        db: Database,
        scanner,
        jellyfin,
        readonly_root: Path,
        jellyfin_database: Path,
        make_thumbnail: Callable[[Path], object],
        *,
        sync_delays: Sequence[float] = (3, 12, 30),
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.db = db
        self.scanner = scanner
        self.jellyfin = jellyfin
        self.root = readonly_root
        self.jellyfin_database = jellyfin_database
        self.make_thumbnail = make_thumbnail
        self.sync_delays = tuple(sync_delays)
        self.sleep = sleep
        self._lock = threading.Lock()

    def start(self, library: str, path: str) -> bool:
        """Run the refresh in the background; False when one is already running."""
        if not self._lock.acquire(blocking=False):
            return False
        threading.Thread(target=self._run_guarded, args=(library, path), daemon=True, name="folder-refresh").start()
        return True

    def _run_guarded(self, library: str, path: str) -> None:
        try:
            self.run(library, path)
        except Exception as exc:  # reported to the user through the job row
            self._job("error", f"Refresh failed: {exc}")
        finally:
            self._lock.release()

    def run(self, library: str, path: str) -> dict:
        if library not in LIBRARIES:
            raise ValueError(f"Unknown library {library!r}")
        label = f"{library}/{path}" if path else library
        notes: list[str] = []

        self._job("running", f"Updating the catalog of {label}")
        indexed = self.scanner.scan_scope(library, path)

        jellyfin_root = self.root / library
        if library in JELLYFIN_LIBRARIES:
            self._job("running", f"Asking Jellyfin to refresh {label}")
            try:
                self.jellyfin.refresh_paths([path], jellyfin_root, self.jellyfin_database)
                for delay in self.sync_delays:
                    self.sleep(delay)
                    self._sync()
            except JellyfinError as exc:
                notes.append(str(exc))

        made, failed = self._thumbnails(library, path, label)
        if library in JELLYFIN_LIBRARIES and not notes:
            # Jellyfin scans asynchronously; a large tree finishes during the thumbnails.
            try:
                self._sync()
            except JellyfinError as exc:
                notes.append(str(exc))

        message = f"Refreshed {label}: {indexed:,} items, {made:,} previews"
        if failed:
            message += f", {failed:,} without preview"
        if notes:
            message += " · " + "; ".join(notes)
        self._job("complete", message, made, made + failed)
        return {"indexed": indexed, "previews": made, "failed": failed, "notes": notes}

    def _sync(self) -> None:
        self.jellyfin.sync_catalog(
            self.db, {name: self.root / name for name in JELLYFIN_LIBRARIES}, self.jellyfin_database
        )

    def _thumbnails(self, library: str, path: str, label: str) -> tuple[int, int]:
        escaped = path.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        with self.db.connect() as db:
            rows = db.execute(
                "SELECT rel_path FROM items WHERE library=? AND kind='file' AND media_type IN (?,?) "
                "AND (?='' OR rel_path=? OR rel_path LIKE ? ESCAPE '\\') ORDER BY rel_path",
                (library, *PREVIEW_TYPES, path, path, f"{escaped}/%"),
            ).fetchall()
        total = len(rows)
        made = failed = 0
        for index, row in enumerate(rows, 1):
            try:
                self.make_thumbnail(self.root / library / row["rel_path"])
                made += 1
            except Exception:  # one unreadable file must not stop the folder
                failed += 1
            if index % 25 == 0 or index == total:
                self._job("running", f"Previews for {label}: {index:,}/{total:,}", index, total)
        return made, failed

    def _job(self, state: str, message: str, current: int = 0, total: int = 0) -> None:
        with self.db.connect() as db:
            db.execute(
                "INSERT INTO jobs(name,state,message,current,total,updated_at) VALUES(?,?,?,?,?,CURRENT_TIMESTAMP) "
                "ON CONFLICT(name) DO UPDATE SET state=excluded.state,message=excluded.message,"
                "current=excluded.current,total=excluded.total,updated_at=CURRENT_TIMESTAMP",
                (JOB, state, message, current, total),
            )
