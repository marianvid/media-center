from pathlib import Path
import pytest

from app.security import UnsafePath, resolve_admin, resolve_library


def test_library_resolution_stays_inside(tmp_path: Path):
    (tmp_path / "video").mkdir()
    target = tmp_path / "video" / "Film.mkv"
    target.touch()
    assert resolve_library(tmp_path, "video", "Film.mkv") == target


@pytest.mark.parametrize("value", ["../secret", "/etc/passwd", "a/../../secret", "%2e%2e/secret"])
def test_library_rejects_escape(tmp_path: Path, value: str):
    (tmp_path / "video").mkdir()
    with pytest.raises(UnsafePath):
        resolve_library(tmp_path, "video", value, must_exist=False)


def test_admin_rejects_escape(tmp_path: Path):
    with pytest.raises(UnsafePath):
        resolve_admin(tmp_path, "../../etc", must_exist=False)
