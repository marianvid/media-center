from __future__ import annotations

import uuid
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode

import httpx


class JellyfinError(RuntimeError):
    pass


class JellyfinClient:
    def __init__(
        self,
        base_url: str,
        public_url: str,
        token: str,
        user_id: str,
        admin_token: str = "",
        *,
        transport: httpx.BaseTransport | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.public_url = public_url.rstrip("/")
        self.token = token
        self.user_id = user_id
        self.admin_token = admin_token
        self._client = httpx.Client(
            base_url=self.base_url or "http://127.0.0.1",
            headers={"X-Emby-Token": token} if token else {},
            timeout=120,
            transport=transport,
        )
        self._admin_client = httpx.Client(
            base_url=self.base_url or "http://127.0.0.1",
            headers={"X-Emby-Token": admin_token} if admin_token else {},
            timeout=120,
            transport=transport,
        )

    @property
    def enabled(self) -> bool:
        return bool(self.base_url and self.public_url and self.token and self.user_id)

    def _request(self, path: str, *, params: dict | None = None) -> dict:
        if not self.enabled:
            raise JellyfinError("Jellyfin playback is not configured")
        try:
            response = self._client.get(path, params=params)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise JellyfinError(f"Jellyfin is unavailable: {exc}") from exc
        return response.json()

    def sync_catalog(self, database, video_root: Path, jellyfin_database: Path) -> dict:
        root = video_root.resolve()
        mappings: dict[str, tuple[str, str]] = {}
        try:
            source_db = sqlite3.connect(f"file:{jellyfin_database}?mode=ro", uri=True, timeout=30)
            rows = source_db.execute(
                "SELECT Id,Path FROM BaseItems "
                "WHERE MediaType='Video' AND IsFolder=0 AND Path IS NOT NULL"
            )
            for item_id, source_path in rows:
                try:
                    relative = Path(source_path).relative_to(root).as_posix()
                except ValueError:
                    continue
                physical_id = str(item_id).replace("-", "").lower()
                mappings[relative] = (physical_id, physical_id)
            source_db.close()
        except (OSError, sqlite3.Error) as exc:
            raise JellyfinError(f"Jellyfin catalog cannot be read: {exc}") from exc

        synced_at = datetime.now(timezone.utc).isoformat()
        with database.connect() as db:
            db.execute(
                "UPDATE items SET jellyfin_item_id='',jellyfin_media_source_id='',jellyfin_synced_at='' "
                "WHERE library='video' AND media_type='video'"
            )
            db.executemany(
                "UPDATE items SET jellyfin_item_id=?,jellyfin_media_source_id=?,jellyfin_synced_at=? "
                "WHERE library='video' AND media_type='video' AND rel_path=?",
                [(item_id, source_id, synced_at, relative) for relative, (item_id, source_id) in mappings.items()],
            )
            matched = db.execute(
                "SELECT count(*) FROM items WHERE library='video' AND media_type='video' AND jellyfin_item_id<>''"
            ).fetchone()[0]
            local_total = db.execute(
                "SELECT count(*) FROM items WHERE library='video' AND media_type='video'"
            ).fetchone()[0]
        return {
            "jellyfin_items": len(mappings),
            "matched": matched,
            "unmatched": local_total - matched,
        }

    def _admin_post(self, path: str, *, params: dict | None = None) -> None:
        if not self.base_url or not self.admin_token:
            raise JellyfinError("Jellyfin administration is not configured")
        try:
            response = self._admin_client.post(path, params=params)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise JellyfinError(f"Jellyfin refresh failed: {exc}") from exc

    def refresh_library(self) -> None:
        self._admin_post("/Library/Refresh")

    def refresh_paths(self, paths: list[str], video_root: Path, jellyfin_database: Path) -> int:
        root = video_root.resolve()
        item_ids: set[str] = set()
        try:
            source_db = sqlite3.connect(f"file:{jellyfin_database}?mode=ro", uri=True, timeout=30)
            for relative in paths:
                candidate = root / relative
                while candidate != root.parent:
                    row = source_db.execute(
                        "SELECT Id FROM BaseItems WHERE Path=? ORDER BY IsFolder DESC LIMIT 1",
                        (str(candidate),),
                    ).fetchone()
                    if row:
                        item_ids.add(str(row[0]).replace("-", "").lower())
                        break
                    if candidate == root:
                        break
                    candidate = candidate.parent
            source_db.close()
        except (OSError, sqlite3.Error) as exc:
            raise JellyfinError(f"Jellyfin catalog cannot be read: {exc}") from exc
        for item_id in item_ids:
            self._admin_post(
                f"/Items/{item_id}/Refresh",
                params={
                    "recursive": "true",
                    "metadataRefreshMode": "Default",
                    "imageRefreshMode": "Default",
                    "replaceAllMetadata": "false",
                    "replaceAllImages": "false",
                },
            )
        return len(item_ids)

    def _item(self, item_id: str) -> dict:
        return self._request(f"/Items/{item_id}", params={"userId": self.user_id})

    @staticmethod
    def _codec(source: dict, stream_type: str) -> str:
        for stream in source.get("MediaStreams", []):
            if stream.get("Type") == stream_type:
                return str(stream.get("Codec") or "").lower()
        return ""

    def playback(self, item_id: str, source_id: str) -> dict:
        if not item_id or not source_id:
            raise JellyfinError("Video is not mapped to Jellyfin yet")
        item = self._item(item_id)
        source = next(
            (candidate for candidate in item.get("MediaSources", []) if candidate.get("Id") == source_id),
            None,
        )
        if source is None:
            raise JellyfinError("The mapped Jellyfin media source is unavailable")
        video_copy = self._codec(source, "Video") == "h264"
        audio_copy = self._codec(source, "Audio") == "aac"
        params = {
            "api_key": self.token,
            "mediaSourceId": source_id,
            "deviceId": f"media-center-web-{uuid.uuid4().hex}",
            "playSessionId": uuid.uuid4().hex,
            "videoCodec": "h264",
            "audioCodec": "aac",
            "maxWidth": 1920,
            "maxHeight": 1080,
            "videoBitRate": 12_000_000,
            "audioBitRate": 192_000,
            "maxAudioChannels": 2,
            "segmentContainer": "mp4",
            "minSegments": 2,
            "breakOnNonKeyFrames": "true",
            "enableAutoStreamCopy": "true",
            "allowVideoStreamCopy": str(video_copy).lower(),
            "allowAudioStreamCopy": str(audio_copy).lower(),
            "requireAvc": "true",
        }
        return {
            "manifest": f"{self.public_url}/Videos/{item_id}/master.m3u8?{urlencode(params)}",
            "engine": "jellyfin",
            "video": "copy" if video_copy else "transcode",
            "audio": "copy" if audio_copy else "transcode",
        }
