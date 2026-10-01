"""Create persistent, non-destructive subtitle timing overrides.

The source media tree is read-only.  This script writes only WebVTT overrides
under the supplied state directory, which Media Center can serve transparently.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from app.files import subtitle_override_path
from app.security import resolve_library


VIDEO_SUFFIXES = {".mkv", ".mp4", ".avi", ".m4v", ".webm"}
SUBTITLE_SUFFIXES = {".srt", ".ass", ".ssa", ".vtt"}
SCORE = re.compile(r"score:\s+([0-9.]+)")
OFFSET = re.compile(r"offset seconds:\s+(-?[0-9.]+)")
SCALE = re.compile(r"framerate scale factor:\s+([0-9.]+)")


def sidecar_for(video: Path) -> Path | None:
    stem = video.stem.casefold()
    return next(
        (
            item
            for item in sorted(video.parent.iterdir())
            if item.suffix.casefold() in SUBTITLE_SUFFIXES
            and (item.stem.casefold() == stem or item.stem.casefold().startswith(stem + "."))
        ),
        None,
    )


def videos_for(root: Path, library: str, paths: list[str]) -> list[Path]:
    videos: list[Path] = []
    for relative in paths:
        target = resolve_library(root, library, relative)
        if target.is_file():
            videos.append(target)
        elif target.is_dir():
            videos.extend(item for item in target.rglob("*") if item.is_file() and item.suffix.casefold() in VIDEO_SUFFIXES)
    return sorted(set(videos))


def run_sync(video: Path, subtitle: Path, output: Path, executable: str) -> dict:
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="subtitle-sync-") as temp_dir:
        synced_srt = Path(temp_dir) / "synced.srt"
        synced_vtt = Path(temp_dir) / "synced.vtt"
        result = subprocess.run(
            [executable, str(video), "-i", str(subtitle), "-o", str(synced_srt)],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=True,
        )
        subprocess.run(
            ["ffmpeg", "-y", "-nostdin", "-v", "error", "-i", str(synced_srt), "-f", "webvtt", str(synced_vtt)],
            check=True,
        )
        shutil.copyfile(synced_vtt, output.with_suffix(".tmp.vtt"))
        output.with_suffix(".tmp.vtt").replace(output)
    log = result.stdout
    return {
        "score": float(SCORE.search(log).group(1)) if SCORE.search(log) else None,
        "offset_seconds": float(OFFSET.search(log).group(1)) if OFFSET.search(log) else None,
        "framerate_scale": float(SCALE.search(log).group(1)) if SCALE.search(log) else None,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Synchronize sidecar subtitles without changing media files.")
    parser.add_argument("--root", type=Path, required=True, help="Root containing the media libraries")
    parser.add_argument("--library", default="video")
    parser.add_argument("--overrides", type=Path, required=True, help="Persistent Media Center state directory for overrides")
    parser.add_argument("--ffsubsync", default="ffsubsync")
    parser.add_argument("--path", action="append", required=True, help="Library-relative video file or directory; may repeat")
    parser.add_argument("--force", action="store_true", help="Regenerate an existing override")
    args = parser.parse_args()

    for video in videos_for(args.root, args.library, args.path):
        subtitle = sidecar_for(video)
        relative = video.relative_to((args.root / args.library).resolve()).as_posix()
        result = {"path": relative, "status": "skipped"}
        if subtitle is None:
            result["reason"] = "no sidecar subtitle"
        else:
            override = subtitle_override_path(video, args.overrides)
            if override.exists() and not args.force:
                result["reason"] = "override already exists"
            else:
                try:
                    result.update(run_sync(video, subtitle, override, args.ffsubsync))
                    result["status"] = "synced"
                except (OSError, subprocess.SubprocessError) as exc:
                    result.update(status="failed", reason=str(exc))
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
