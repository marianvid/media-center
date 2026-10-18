from pathlib import Path

import pytest

from app.admin_jobs import AdminIndexer
from app.jellyfin import JellyfinError
from app.media_types import browser_playable, media_type
from app.playback import PlaybackPlanner, PlaybackUnavailable


class FakeJellyfin:
    def __init__(self, error: str = ""):
        self.error = error
        self.calls: list[tuple[str, str]] = []

    def playback(self, item_id: str, source_id: str) -> dict:
        self.calls.append((item_id, source_id))
        if self.error:
            raise JellyfinError(self.error)
        return {"engine": "jellyfin", "manifest": f"http://media/{item_id}.m3u8"}


def planner(mappings=None, jellyfin=None):
    mappings = mappings or {}
    return PlaybackPlanner(lambda library, path: mappings.get((library, path)), jellyfin or FakeJellyfin())


@pytest.mark.parametrize("name", ["Video0002.3gp", "clip.3G2", "MihailCanta.MTS", "VTS_01_1.VOB"])
def test_phone_camcorder_and_dvd_files_are_videos(name):
    assert media_type(Path(name)) == "video"


@pytest.mark.parametrize(("name", "direct"), [("a.mp4", True), ("a.MOV", True), ("a.webm", True), ("a.3gp", False), ("a.mts", False), ("a.mkv", False)])
def test_browser_playable_containers(name, direct):
    assert browser_playable(Path(name)) is direct


def test_memories_mp4_plays_directly_without_jellyfin():
    jellyfin = FakeJellyfin()

    plan = planner(jellyfin=jellyfin).plan("amintiri", "2018/Sibiu/a b.mp4", Path("a b.mp4"))

    assert plan == {"engine": "direct", "url": "/api/media/amintiri?path=2018/Sibiu/a%20b.mp4"}
    assert jellyfin.calls == []


def test_memories_3gp_plays_through_jellyfin():
    jellyfin = FakeJellyfin()
    mappings = {("amintiri", "Telefon/Video0002.3gp"): ("clip", "clip")}

    plan = planner(mappings, jellyfin).plan("amintiri", "Telefon/Video0002.3gp", Path("Video0002.3gp"))

    assert plan["engine"] == "jellyfin"
    assert jellyfin.calls == [("clip", "clip")]


def test_video_library_always_uses_jellyfin_even_for_mp4():
    jellyfin = FakeJellyfin()
    mappings = {("video", "Film/film.mp4"): ("film", "film")}

    planner(mappings, jellyfin).plan("video", "Film/film.mp4", Path("film.mp4"))

    assert jellyfin.calls == [("film", "film")]


@pytest.mark.parametrize("mapping", [None, ("", ""), ("item", "")])
def test_unmapped_video_is_reported_as_unavailable(mapping):
    mappings = {("amintiri", "a.mts"): mapping} if mapping else {}

    with pytest.raises(PlaybackUnavailable, match="not mapped") as error:
        planner(mappings).plan("amintiri", "a.mts", Path("a.mts"))

    assert error.value.status == 503


def test_audio_library_has_no_planned_playback():
    with pytest.raises(PlaybackUnavailable) as error:
        planner().plan("audio", "song.mp4", Path("song.mp4"))

    assert error.value.status == 400


def test_jellyfin_failure_becomes_unavailable():
    mappings = {("amintiri", "a.vob"): ("dvd", "dvd")}

    with pytest.raises(PlaybackUnavailable, match="down") as error:
        planner(mappings, FakeJellyfin("Jellyfin is down")).plan("amintiri", "a.vob", Path("a.vob"))

    assert error.value.status == 503


def test_admin_paths_are_grouped_per_jellyfin_library():
    grouped = AdminIndexer._jellyfin_paths(["video/Film", "amintiri", "amintiri/Telefon/a.3gp", "audio/x", "videos/y"])

    assert grouped == {"video": ["Film"], "amintiri": ["amintiri", "Telefon/a.3gp"]}
