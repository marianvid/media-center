import importlib.util
import io
from pathlib import Path


def load_helper():
    path = Path(__file__).parents[1] / "deploy" / "media-admin.py"
    spec = importlib.util.spec_from_file_location("media_admin", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_helper_rejects_escape(tmp_path, monkeypatch):
    helper = load_helper()
    monkeypatch.setattr(helper, "ROOT", tmp_path.resolve())
    try:
        helper.resolve_relative("../etc/passwd")
        assert False, "escape should fail"
    except ValueError:
        pass


def test_helper_rename_and_confirmed_delete(tmp_path, monkeypatch):
    helper = load_helper()
    monkeypatch.setattr(helper, "ROOT", tmp_path.resolve())
    source = tmp_path / "old.txt"
    source.write_text("safe")
    result = helper.perform({"action": "rename", "path": "old.txt", "new_name": "new.txt"})
    assert result["path"] == "new.txt"
    assert (tmp_path / "new.txt").exists()
    helper.perform({"action": "delete", "path": "new.txt", "confirm_name": "new.txt"})
    assert not (tmp_path / "new.txt").exists()


def test_helper_mkdir_move_and_streamed_upload(tmp_path, monkeypatch):
    helper = load_helper()
    monkeypatch.setattr(helper, "ROOT", tmp_path.resolve())
    helper.perform({"action": "mkdir", "path": "", "name": "Video"})
    source = tmp_path / "clip.mkv"
    source.write_bytes(b"media")
    moved = helper.perform({"action": "move", "path": "clip.mkv", "destination": "Video"})
    assert moved["path"] == "Video/clip.mkv"
    result = helper.receive_upload(
        {"path": "Video", "name": "new.mkv", "size": 7}, io.BytesIO(b"content")
    )
    assert result["path"] == "Video/new.mkv"
    assert (tmp_path / "Video" / "new.mkv").read_bytes() == b"content"


def test_failed_upload_removes_partial_file(tmp_path, monkeypatch):
    helper = load_helper()
    monkeypatch.setattr(helper, "ROOT", tmp_path.resolve())
    try:
        helper.receive_upload({"path": "", "name": "bad.mkv", "size": 10}, io.BytesIO(b"short"))
        assert False, "short upload should fail"
    except ValueError:
        pass
    assert not list(tmp_path.iterdir())


def test_library_roots_are_protected(tmp_path, monkeypatch):
    helper = load_helper()
    monkeypatch.setattr(helper, "ROOT", tmp_path.resolve())
    (tmp_path / "video").mkdir()
    for action in ("rename", "move", "delete"):
        try:
            helper.perform({"action": action, "path": "video", "new_name": "films", "destination": "", "confirm_name": "video"})
            assert False, f"{action} should be rejected"
        except ValueError:
            pass
    assert (tmp_path / "video").is_dir()
