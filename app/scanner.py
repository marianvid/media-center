from __future__ import annotations

import os
import re
import threading
from pathlib import Path

from .db import Database
from .media_metadata import image_year, read_audio_metadata
from .media_types import media_type


TITLE_YEAR = re.compile(r"^(?P<title>.*?)[ ._-]*[\(\[](?P<year>19\d{2}|20\d{2})(?:[^\)\]]*)[\)\]]")
NOISE = re.compile(r"[ ._-](?:2160p|1080p|720p|576p|480p|BluRay|WEB[- .]?DL|HEVC|x26[45]|AAC|DD|DTS).*$", re.I)


def title_from_name(name: str) -> tuple[str, int | None]:
    stem = Path(name).stem
    match = TITLE_YEAR.match(stem)
    if match:
        return match.group("title").replace(".", " ").strip(), int(match.group("year"))
    return NOISE.sub("", stem).replace(".", " ").strip(), None


class CatalogScanner:
    def __init__(self, db: Database, readonly_root: Path):
        self.db = db
        self.root = readonly_root
        self._lock = threading.Lock()

    def start(self) -> bool:
        if not self._lock.acquire(blocking=False):
            return False
        threading.Thread(target=self._run_guarded, daemon=True, name="catalog-scan").start()
        return True

    def run_blocking(self) -> None:
        with self._lock:
            self.scan()

    def _run_guarded(self) -> None:
        try:
            self.scan()
        finally:
            self._lock.release()

    def _job(self, state: str, message: str, current: int = 0) -> None:
        with self.db.connect() as db:
            db.execute(
                "INSERT INTO jobs(name,state,message,current,updated_at) VALUES('scan',?,?,?,CURRENT_TIMESTAMP) "
                "ON CONFLICT(name) DO UPDATE SET state=excluded.state,message=excluded.message,current=excluded.current,updated_at=CURRENT_TIMESTAMP",
                (state, message, current),
            )

    def scan(self) -> None:
        self._job("running", "Scanning media libraries")
        seen: set[tuple[str, str]] = set()
        count = 0
        with self.db.connect() as db:
            for library in ("amintiri", "audio", "video"):
                base = self.root / library
                if not base.exists():
                    continue
                for current, directories, files in os.walk(base):
                    directories[:] = [d for d in directories if not d.startswith(".")]
                    parent = Path(current)
                    for name in [*directories, *files]:
                        if name.startswith("._") or name in {".DS_Store", "Thumbs.db"}:
                            continue
                        path = parent / name
                        rel = path.relative_to(base).as_posix()
                        parent_rel = path.parent.relative_to(base).as_posix()
                        if parent_rel == ".":
                            parent_rel = ""
                        try:
                            stat = path.stat()
                        except (FileNotFoundError, PermissionError):
                            continue
                        kind = "directory" if path.is_dir() else "file"
                        mtype = media_type(path)
                        title, year = title_from_name(name)
                        if library == "amintiri" and kind == "file" and mtype == "image":
                            year = image_year(rel, stat.st_mtime, year)
                        db.execute(
                            "INSERT INTO items(library,rel_path,parent_path,name,kind,media_type,size,mtime,title,year) "
                            "VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(library,rel_path) DO UPDATE SET "
                            "parent_path=excluded.parent_path,name=excluded.name,kind=excluded.kind,media_type=excluded.media_type,"
                            "size=excluded.size,mtime=excluded.mtime,"
                            "title=CASE WHEN items.imdb_match_status LIKE 'manual%' THEN items.title ELSE excluded.title END,"
                            "year=CASE WHEN items.imdb_match_status LIKE 'manual%' THEN items.year "
                            "WHEN items.title=excluded.title THEN coalesce(excluded.year,items.year) ELSE excluded.year END,"
                            "updated_at=CURRENT_TIMESTAMP",
                            (library, rel, parent_rel, name, kind, mtype, stat.st_size if kind == "file" else 0, stat.st_mtime, title, year),
                        )
                        seen.add((library, rel))
                        count += 1
                        if count % 1000 == 0:
                            db.commit()
                            self._job("running", f"Indexed {count:,} items", count)
            rows = db.execute("SELECT library,rel_path FROM items").fetchall()
            stale = [(r["library"], r["rel_path"]) for r in rows if (r["library"], r["rel_path"]) not in seen]
            db.executemany("DELETE FROM items WHERE library=? AND rel_path=?", stale)
        self.enrich_audio_metadata()
        self._job("complete", f"Indexed {count:,} items", count)

    def enrich_audio_metadata(self) -> int:
        with self.db.connect() as db:
            rows = db.execute(
                "SELECT rel_path,mtime FROM items WHERE library='audio' AND media_type='audio' "
                "AND metadata_mtime<>mtime ORDER BY rel_path"
            ).fetchall()
        total = len(rows)
        if not total:
            return 0
        self._job("running", f"Reading audio metadata: 0/{total:,}", 0)
        updated = 0
        with self.db.connect() as db:
            for index, row in enumerate(rows, 1):
                path = self.root / "audio" / row["rel_path"]
                try:
                    year, genres, artists = read_audio_metadata(path, row["rel_path"], row["mtime"])
                except (FileNotFoundError, PermissionError):
                    continue
                db.execute(
                    "UPDATE items SET year=?,genres=?,actors=?,metadata_mtime=?,updated_at=CURRENT_TIMESTAMP "
                    "WHERE library='audio' AND rel_path=?",
                    (year, genres, artists, row["mtime"], row["rel_path"]),
                )
                updated += 1
                if index % 250 == 0:
                    db.commit()
                    self._job("running", f"Reading audio metadata: {index:,}/{total:,}", index)
        return updated
