from pathlib import Path

from app.db import Database
from app.scanner import CatalogScanner, title_from_name


def test_title_parser():
    assert title_from_name("Blade Runner (1982).1080p.x265.mkv") == ("Blade Runner", 1982)
    assert title_from_name("Puss Gets The Boot [1939]-480p.x265.mkv") == ("Puss Gets The Boot", 1939)
    assert title_from_name("Stargate SG-1 (1997-2007)") == ("Stargate SG-1", 1997)


def test_scan_indexes_media(tmp_path: Path):
    root = tmp_path / "media"
    for name in ("amintiri", "audio", "video"):
        (root / name).mkdir(parents=True)
    (root / "video" / "Alien (1979).1080p.mkv").write_bytes(b"movie")
    db = Database(tmp_path / "catalog.db")
    db.initialize()
    CatalogScanner(db, root).scan()
    with db.connect() as conn:
        row = conn.execute("SELECT * FROM items WHERE library='video'").fetchone()
    assert row["title"] == "Alien"
    assert row["year"] == 1979
    assert row["media_type"] == "video"


def test_scan_preserves_manually_confirmed_imdb_title(tmp_path: Path):
    root = tmp_path / "media"
    for name in ("amintiri", "audio", "video"):
        (root / name).mkdir(parents=True)
    (root / "video" / "Avatar (2022).The Way of Water.mkv").write_bytes(b"movie")
    db = Database(tmp_path / "catalog.db")
    db.initialize()
    scanner = CatalogScanner(db, root)
    scanner.scan()
    with db.connect() as conn:
        conn.execute(
            "UPDATE items SET title='Avatar: The Way of Water',year=2022,imdb_match_status='manual' "
            "WHERE library='video'"
        )

    scanner.scan()

    with db.connect() as conn:
        row = conn.execute("SELECT title,year,imdb_match_status FROM items WHERE library='video'").fetchone()
    assert row["title"] == "Avatar: The Way of Water"
    assert row["year"] == 2022
    assert row["imdb_match_status"] == "manual"
