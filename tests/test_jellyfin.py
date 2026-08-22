from pathlib import Path
import sqlite3
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from app.db import Database
from app.jellyfin import JellyfinClient, JellyfinError


SOURCE = Path("/srv/media/readonly/video/Series/episode.mkv")


def client_for(video_codec="hevc", audio_codec="vorbis"):
    def handler(request: httpx.Request):
        assert request.headers["X-Emby-Token"] == "playback-token"
        assert request.url.path == "/Items/item-id"
        return httpx.Response(
            200,
            json={
                "Id": "item-id",
                "MediaSources": [
                    {
                        "Id": "other-source",
                        "MediaStreams": [
                            {"Type": "Video", "Codec": "h264"},
                            {"Type": "Audio", "Codec": "aac"},
                        ],
                    },
                    {
                        "Id": "source-id",
                        "Path": str(SOURCE),
                        "MediaStreams": [
                            {"Type": "Video", "Codec": video_codec},
                            {"Type": "Audio", "Codec": audio_codec},
                        ],
                    },
                ],
            },
        )

    return JellyfinClient(
        "http://jellyfin:8096",
        "http://media:8096",
        "playback-token",
        "user-id",
        transport=httpx.MockTransport(handler),
    )


def test_hevc_vorbis_uses_jellyfin_h264_aac_transcoding():
    playback = client_for().playback("item-id", "source-id")
    query = parse_qs(urlparse(playback["manifest"]).query)

    assert playback["engine"] == "jellyfin"
    assert playback["video"] == "transcode"
    assert playback["audio"] == "transcode"
    assert query["videoCodec"] == ["h264"]
    assert query["audioCodec"] == ["aac"]
    assert query["allowVideoStreamCopy"] == ["false"]
    assert query["allowAudioStreamCopy"] == ["false"]
    assert query["maxWidth"] == ["1920"]


def test_h264_aac_allows_stream_copy():
    playback = client_for("h264", "aac").playback("item-id", "source-id")
    query = parse_qs(urlparse(playback["manifest"]).query)

    assert playback["video"] == "copy"
    assert playback["audio"] == "copy"
    assert query["allowVideoStreamCopy"] == ["true"]
    assert query["allowAudioStreamCopy"] == ["true"]


def test_missing_jellyfin_source_is_reported():
    client = client_for()

    with pytest.raises(JellyfinError, match="media source is unavailable"):
        client.playback("item-id", "missing-source")


def test_sync_catalog_maps_duplicate_names_by_full_path(tmp_path):
    video_root = Path("/srv/media/readonly/video")
    paths = ["Series A/episode.mkv", "Series B/episode.mkv"]
    jellyfin_database = tmp_path / "jellyfin.db"
    with sqlite3.connect(jellyfin_database) as source:
        source.execute(
            "CREATE TABLE BaseItems(Id TEXT,Path TEXT,MediaType TEXT,IsFolder INTEGER)"
        )
        source.executemany(
            "INSERT INTO BaseItems VALUES(?,?, 'Video',0)",
            [
                ("ITEM-A", str(video_root / paths[0])),
                ("ITEM-B", str(video_root / paths[1])),
            ],
        )
    database = Database(tmp_path / "catalog.db")
    database.initialize()
    with database.connect() as db:
        db.executemany(
            "INSERT INTO items(library,rel_path,parent_path,name,kind,media_type) "
            "VALUES('video',?,?,?,?, 'video')",
            [(path, str(Path(path).parent), Path(path).name, "file") for path in paths],
        )

    client = JellyfinClient(
        "http://jellyfin:8096",
        "http://media:8096",
        "playback-token",
        "user-id",
    )
    result = client.sync_catalog(database, video_root, jellyfin_database)

    assert result == {"jellyfin_items": 2, "matched": 2, "unmatched": 0}
    with database.connect() as db:
        rows = db.execute(
            "SELECT rel_path,jellyfin_item_id,jellyfin_media_source_id FROM items ORDER BY rel_path"
        ).fetchall()
    assert [tuple(row) for row in rows] == [
        (paths[0], "itema", "itema"),
        (paths[1], "itemb", "itemb"),
    ]
