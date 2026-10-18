from __future__ import annotations

import mimetypes
from pathlib import Path


IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".tif", ".tiff", ".heic", ".avif"}
AUDIO_EXT = {".mp3", ".m4a", ".aac", ".flac", ".wav", ".ogg", ".opus", ".wma", ".alac"}
VIDEO_EXT = {
    ".mp4", ".mkv", ".avi", ".mov", ".m4v", ".webm", ".ts", ".m2ts", ".mpg", ".mpeg", ".wmv",
    # Phone, camcorder and DVD formats found in family archives.
    ".3gp", ".3g2", ".mts", ".vob",
}
# Containers that every current browser opens directly in a <video> element.
# Anything else needs Jellyfin to repackage or transcode it first.
BROWSER_VIDEO_EXT = {".mp4", ".m4v", ".mov", ".webm"}


def media_type(path: Path) -> str:
    if path.is_dir():
        return "directory"
    ext = path.suffix.lower()
    if ext in IMAGE_EXT:
        return "image"
    if ext in AUDIO_EXT:
        return "audio"
    if ext in VIDEO_EXT:
        return "video"
    return "other"


def browser_playable(path: Path) -> bool:
    """True when a browser can play the video file as it is, without Jellyfin."""
    return path.suffix.lower() in BROWSER_VIDEO_EXT


def content_type(path: Path) -> str:
    return mimetypes.guess_type(path.name)[0] or "application/octet-stream"
