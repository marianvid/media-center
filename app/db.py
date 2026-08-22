from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path


SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS items (
  id INTEGER PRIMARY KEY,
  library TEXT NOT NULL,
  rel_path TEXT NOT NULL,
  parent_path TEXT NOT NULL,
  name TEXT NOT NULL,
  kind TEXT NOT NULL,
  media_type TEXT NOT NULL,
  size INTEGER NOT NULL DEFAULT 0,
  mtime REAL NOT NULL DEFAULT 0,
  title TEXT,
  year INTEGER,
  imdb_id TEXT,
  imdb_rating REAL,
  genres TEXT NOT NULL DEFAULT '',
  actors TEXT NOT NULL DEFAULT '',
  imdb_match_status TEXT NOT NULL DEFAULT '',
  synopsis TEXT NOT NULL DEFAULT '',
  synopsis_language TEXT NOT NULL DEFAULT '',
  synopsis_source TEXT NOT NULL DEFAULT '',
  synopsis_retrieved_at TEXT NOT NULL DEFAULT '',
  metadata_mtime REAL NOT NULL DEFAULT 0,
  jellyfin_item_id TEXT NOT NULL DEFAULT '',
  jellyfin_media_source_id TEXT NOT NULL DEFAULT '',
  jellyfin_synced_at TEXT NOT NULL DEFAULT '',
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(library, rel_path)
);
CREATE INDEX IF NOT EXISTS idx_items_parent ON items(library, parent_path);
CREATE INDEX IF NOT EXISTS idx_items_media ON items(library, media_type);
CREATE INDEX IF NOT EXISTS idx_items_title ON items(title);
CREATE INDEX IF NOT EXISTS idx_items_imdb_id ON items(imdb_id);
CREATE VIRTUAL TABLE IF NOT EXISTS item_search USING fts5(
  name, title, genres, actors, content='items', content_rowid='id'
);
CREATE TRIGGER IF NOT EXISTS items_ai AFTER INSERT ON items BEGIN
  INSERT INTO item_search(rowid,name,title,genres,actors)
  VALUES(new.id,new.name,coalesce(new.title,''),new.genres,new.actors);
END;
CREATE TRIGGER IF NOT EXISTS items_ad AFTER DELETE ON items BEGIN
  INSERT INTO item_search(item_search,rowid,name,title,genres,actors)
  VALUES('delete',old.id,old.name,coalesce(old.title,''),old.genres,old.actors);
END;
CREATE TRIGGER IF NOT EXISTS items_au AFTER UPDATE ON items BEGIN
  INSERT INTO item_search(item_search,rowid,name,title,genres,actors)
  VALUES('delete',old.id,old.name,coalesce(old.title,''),old.genres,old.actors);
  INSERT INTO item_search(rowid,name,title,genres,actors)
  VALUES(new.id,new.name,coalesce(new.title,''),new.genres,new.actors);
END;
CREATE TABLE IF NOT EXISTS jobs (
  name TEXT PRIMARY KEY,
  state TEXT NOT NULL,
  message TEXT NOT NULL DEFAULT '',
  current INTEGER NOT NULL DEFAULT 0,
  total INTEGER NOT NULL DEFAULT 0,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""


class Database:
    def __init__(self, path: Path):
        self.path = path

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript(SCHEMA)
            columns = {row["name"] for row in db.execute("PRAGMA table_info(items)")}
            migrations = {
                "synopsis": "TEXT NOT NULL DEFAULT ''",
                "synopsis_language": "TEXT NOT NULL DEFAULT ''",
                "synopsis_source": "TEXT NOT NULL DEFAULT ''",
                "synopsis_retrieved_at": "TEXT NOT NULL DEFAULT ''",
                "imdb_match_status": "TEXT NOT NULL DEFAULT ''",
                "metadata_mtime": "REAL NOT NULL DEFAULT 0",
                "jellyfin_item_id": "TEXT NOT NULL DEFAULT ''",
                "jellyfin_media_source_id": "TEXT NOT NULL DEFAULT ''",
                "jellyfin_synced_at": "TEXT NOT NULL DEFAULT ''",
            }
            for name, definition in migrations.items():
                if name not in columns:
                    db.execute(f"ALTER TABLE items ADD COLUMN {name} {definition}")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        try:
            yield db
            db.commit()
        finally:
            db.close()
