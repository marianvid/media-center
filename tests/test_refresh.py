import threading
from pathlib import Path

import pytest

from app.db import Database
from app.jellyfin import JellyfinError
from app.refresh import FolderRefresher
from app.scanner import CatalogScanner


def make_tree(root: Path) -> None:
    for rel in ["amintiri/Telefon/Video0002.3gp", "amintiri/Telefon/Photo0001.jpg", "amintiri/Telefon/notes.txt",
                "amintiri/2018/Sibiu/clip.MTS", "audio/Album/song.mp3"]:
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x")


@pytest.fixture
def catalog(tmp_path):
    root = tmp_path / "readonly"
    make_tree(root)
    db = Database(tmp_path / "catalog.db")
    db.initialize()
    return root, db, CatalogScanner(db, root)


class FakeJellyfin:
    def __init__(self, fail: bool = False):
        self.fail = fail
        self.refreshed: list[tuple[list[str], Path]] = []
        self.syncs = 0

    def refresh_paths(self, paths, root, database):
        if self.fail:
            raise JellyfinError("Jellyfin refresh failed: down")
        self.refreshed.append((paths, root))
        return 1

    def sync_catalog(self, db, roots, database):
        self.syncs += 1
        assert set(roots) == {"video", "amintiri"}
        return {}


def refresher(root, db, scanner, jellyfin, made):
    return FolderRefresher(db, scanner, jellyfin, root, Path("/jf.db"), made.append, sync_delays=(0, 0), sleep=lambda _: None)


def job(db):
    with db.connect() as conn:
        return dict(conn.execute("SELECT * FROM jobs WHERE name='folder_refresh'").fetchone())


def test_refresh_of_a_folder_covers_only_that_folder(catalog):
    root, db, scanner = catalog
    jellyfin, made = FakeJellyfin(), []

    result = refresher(root, db, scanner, jellyfin, made).run("amintiri", "Telefon")

    assert sorted(p.name for p in made) == ["Photo0001.jpg", "Video0002.3gp"]
    assert jellyfin.refreshed == [(["Telefon"], root / "amintiri")]
    assert jellyfin.syncs == 3
    assert result["previews"] == 2 and result["failed"] == 0
    with db.connect() as conn:
        paths = {r[0] for r in conn.execute("SELECT library||'/'||rel_path FROM items")}
    assert "amintiri/2018/Sibiu/clip.MTS" not in paths
    assert job(db)["state"] == "complete"


def test_refresh_at_library_root_covers_the_whole_library(catalog):
    root, db, scanner = catalog
    made = []

    refresher(root, db, scanner, FakeJellyfin(), made).run("amintiri", "")

    assert sorted(p.name for p in made) == ["Photo0001.jpg", "Video0002.3gp", "clip.MTS"]


def test_audio_refresh_skips_jellyfin_and_previews(catalog):
    root, db, scanner = catalog
    jellyfin, made = FakeJellyfin(), []

    result = refresher(root, db, scanner, jellyfin, made).run("audio", "")

    assert jellyfin.refreshed == [] and jellyfin.syncs == 0
    assert made == [] and result["indexed"] == 2


def test_jellyfin_failure_is_reported_and_previews_still_made(catalog):
    root, db, scanner = catalog
    made = []

    result = refresher(root, db, scanner, FakeJellyfin(fail=True), made).run("amintiri", "Telefon")

    assert len(made) == 2
    assert "down" in job(db)["message"] and result["notes"]


def test_unreadable_file_counts_as_without_preview(catalog):
    root, db, scanner = catalog

    def broken(path):
        raise OSError("bad file")

    service = FolderRefresher(db, scanner, FakeJellyfin(), root, Path("/jf.db"), broken, sync_delays=(), sleep=lambda _: None)
    result = service.run("amintiri", "Telefon")

    assert result == {"indexed": 3, "previews": 0, "failed": 2, "notes": []}
    assert "2 without preview" in job(db)["message"]


def test_unknown_library_is_rejected(catalog):
    root, db, scanner = catalog
    with pytest.raises(ValueError):
        refresher(root, db, scanner, FakeJellyfin(), []).run("etc", "")


def test_only_one_refresh_runs_at_a_time(catalog):
    root, db, scanner = catalog
    gate, finished = threading.Event(), threading.Event()

    def slow(path):
        gate.wait(5)

    service = FolderRefresher(db, scanner, FakeJellyfin(), root, Path("/jf.db"), slow, sync_delays=(), sleep=lambda _: None)
    original = service.run
    service.run = lambda *args: (original(*args), finished.set())

    assert service.start("amintiri", "Telefon") is True
    assert service.start("amintiri", "") is False
    gate.set()
    assert finished.wait(5)


def test_failed_background_refresh_is_written_to_the_job(catalog):
    root, db, scanner = catalog
    service = refresher(root, db, scanner, FakeJellyfin(), [])
    service._lock.acquire()  # as start() does before handing over to the thread

    service._run_guarded("etc", "")

    assert job(db)["state"] == "error"
    assert service.start("amintiri", "") is True  # the lock was released
