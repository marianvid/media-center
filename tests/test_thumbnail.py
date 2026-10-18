import shutil
import subprocess
from pathlib import Path

import pytest

from app import files


def test_short_clip_falls_back_to_an_earlier_frame(tmp_path: Path, monkeypatch):
    offsets = []

    def fake_run(command, check, timeout):
        offset = command[command.index("-ss") + 1]
        offsets.append(offset)
        if offset == "1":
            Path(command[-1]).write_bytes(b"jpeg")
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(files.subprocess, "run", fake_run)
    target = tmp_path / "frame.jpg"

    files.video_frame(tmp_path / "Video0002.3gp", target, 480)

    assert offsets == ["15", "1"]
    assert target.read_bytes() == b"jpeg"


def test_video_without_any_frame_raises_os_error(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(files.subprocess, "run", lambda command, check, timeout: subprocess.CompletedProcess(command, 1))

    with pytest.raises(OSError):
        files.video_frame(tmp_path / "broken.mts", tmp_path / "frame.jpg", 480)


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_real_three_second_clip_gets_a_thumbnail(tmp_path: Path):
    clip = tmp_path / "Video0001.3gp"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc=duration=3:size=176x144:rate=15",
         "-c:v", "mpeg4", "-y", str(clip)],
        check=True,
    )

    response = files.thumbnail(clip, tmp_path / "cache", 480)

    assert response.media_type == "image/jpeg"
    assert response.body[:2] == b"\xff\xd8"
