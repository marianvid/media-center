"""Decide how a catalog video reaches the browser.

Design: Strategy. Each library has a playback policy:

- ``video``: always through Jellyfin (HLS, with VA-API transcoding when the
  codecs need it);
- ``amintiri`` (Memories): containers every browser opens directly
  (``.mp4``, ``.mov`` ...) are streamed as they are; formats a browser cannot
  decode (``.3gp``, ``.mts``, ``.vob`` ...) go through Jellyfin.

This module holds domain logic only. The catalog lookup and the Jellyfin
client are passed in (dependency injection), so the policy is tested without
a database or a Jellyfin server. See docs/decisions/0001-memories-video-playback.md.
"""
from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Protocol
from urllib.parse import quote

from .jellyfin import JellyfinError
from .media_types import browser_playable


# Catalog libraries that Jellyfin indexes; their videos can be played through it.
JELLYFIN_LIBRARIES = ("video", "amintiri")
# Libraries where the browser plays a file directly whenever it can.
DIRECT_FIRST_LIBRARIES = frozenset({"amintiri"})

# (jellyfin_item_id, jellyfin_media_source_id) for a catalog item, or None.
MappingLookup = Callable[[str, str], "tuple[str, str] | None"]


class PlaybackSource(Protocol):
    def playback(self, item_id: str, source_id: str) -> dict: ...


class PlaybackUnavailable(Exception):
    """Playback cannot start; ``status`` is the HTTP status to report."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


class PlaybackPlanner:
    def __init__(self, lookup: MappingLookup, jellyfin: PlaybackSource):
        self._lookup = lookup
        self._jellyfin = jellyfin

    def plan(self, library: str, rel_path: str, file_path: Path) -> dict:
        """Return ``{"engine": "direct", "url": ...}`` or Jellyfin's HLS session."""
        if library not in JELLYFIN_LIBRARIES:
            raise PlaybackUnavailable(400, "Playback is only available for the video and amintiri libraries")
        if library in DIRECT_FIRST_LIBRARIES and browser_playable(file_path):
            return {"engine": "direct", "url": f"/api/media/{library}?path={quote(rel_path)}"}
        mapping = self._lookup(library, rel_path)
        if mapping is None or not mapping[0] or not mapping[1]:
            raise PlaybackUnavailable(503, "Video is not mapped to Jellyfin yet")
        try:
            return self._jellyfin.playback(*mapping)
        except JellyfinError as exc:
            raise PlaybackUnavailable(503, str(exc)) from exc
