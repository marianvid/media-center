import os
import zipfile
from pathlib import Path

from app.files import _directory_archive, subtitle_override_path


def test_directory_archive_contains_selected_folder(tmp_path: Path):
    source = tmp_path / "Holiday"
    (source / "photos").mkdir(parents=True)
    (source / "photos" / "one.jpg").write_bytes(b"image")
    (source / "empty").mkdir()
    destination = tmp_path / "holiday.zip"

    _directory_archive(source, destination)

    with zipfile.ZipFile(destination) as archive:
        assert set(archive.namelist()) == {
            "Holiday/",
            "Holiday/empty/",
            "Holiday/photos/",
            "Holiday/photos/one.jpg",
        }
        assert archive.read("Holiday/photos/one.jpg") == b"image"


def test_directory_archive_does_not_follow_symlinks(tmp_path: Path):
    source = tmp_path / "Music"
    source.mkdir()
    outside = tmp_path / "secret.txt"
    outside.write_text("secret")
    (source / "shortcut").symlink_to(outside)
    destination = tmp_path / "music.zip"

    _directory_archive(source, destination)

    with zipfile.ZipFile(destination) as archive:
        assert archive.namelist() == ["Music/"]


def test_subtitle_override_path_changes_with_the_media_revision(tmp_path: Path):
    video = tmp_path / "Episode.mkv"
    video.write_bytes(b"first")
    overrides = tmp_path / "overrides"
    first = subtitle_override_path(video, overrides)
    stat = video.stat()
    video.write_bytes(b"second")
    os.utime(video, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1))

    second = subtitle_override_path(video, overrides)

    assert first.parent == overrides
    assert first.suffix == ".vtt"
    assert first != second
