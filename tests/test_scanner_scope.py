from pathlib import Path

import pytest

from app.db import Database
from app.scanner import CatalogScanner


def rows(db):
    with db.connect() as conn:
        return {(r["library"], r["rel_path"]): r["media_type"] for r in conn.execute("SELECT library,rel_path,media_type FROM items")}


def setup(tmp_path):
    root = tmp_path / "readonly"
    for rel in ["amintiri/A/one.jpg", "amintiri/A/sub/clip.3gp", "amintiri/B/two.jpg", "video/Film/film.mkv"]:
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_bytes(b"x")
    db = Database(tmp_path / "catalog.db")
    db.initialize()
    return root, db, CatalogScanner(db, root)


def test_full_scan_indexes_every_library(tmp_path):
    root, db, scanner = setup(tmp_path)

    scanner.scan()

    assert rows(db)[("amintiri", "A/sub/clip.3gp")] == "video"
    assert ("video", "Film/film.mkv") in rows(db)


def test_scoped_scan_removes_stale_rows_only_inside_its_folder(tmp_path):
    root, db, scanner = setup(tmp_path)
    scanner.scan()
    (root / "amintiri/A/one.jpg").unlink()
    (root / "amintiri/B/two.jpg").unlink()
    (root / "amintiri/A/new.mts").write_bytes(b"x")

    seen = scanner.scan_scope("amintiri", "A")

    catalog = rows(db)
    assert ("amintiri", "A/one.jpg") not in catalog
    assert catalog[("amintiri", "A/new.mts")] == "video"
    assert ("amintiri", "A") in catalog
    assert ("amintiri", "B/two.jpg") in catalog  # outside the scope: untouched until refreshed
    assert seen == 3  # new.mts, sub, sub/clip.3gp


def test_scope_with_a_similar_prefix_is_not_touched(tmp_path):
    root, db, scanner = setup(tmp_path)
    (root / "amintiri/AB").mkdir()
    (root / "amintiri/AB/keep.jpg").write_bytes(b"x")
    scanner.scan()
    (root / "amintiri/AB/keep.jpg").unlink()

    scanner.scan_scope("amintiri", "A")

    assert ("amintiri", "AB/keep.jpg") in rows(db)


def test_missing_folder_and_unknown_library(tmp_path):
    root, db, scanner = setup(tmp_path)

    assert scanner.scan_scope("amintiri", "does-not-exist") == 0
    with pytest.raises(ValueError):
        scanner.scan_scope("etc")
