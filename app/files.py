from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path

from fastapi import HTTPException, Request
from fastapi.responses import FileResponse, Response, StreamingResponse
from PIL import Image, ImageOps
from charset_normalizer import from_bytes

from .media_types import content_type, media_type


# Video previews are relatively expensive.  Keeping this deliberately small
# prevents a catalog page from starving an active playback preparation job.
_THUMBNAIL_SLOTS = threading.BoundedSemaphore(1)


def describe(path: Path, base: Path) -> dict:
    stat = path.stat()
    return {
        "name": path.name,
        "path": "" if path == base else path.relative_to(base).as_posix(),
        "kind": "directory" if path.is_dir() else "file",
        "media_type": media_type(path),
        "size": stat.st_size if path.is_file() else 0,
        "mtime": stat.st_mtime,
    }


def list_directory(path: Path, base: Path) -> list[dict]:
    entries = []
    for item in path.iterdir():
        if item.name.startswith("._") or item.name in {".DS_Store", "Thumbs.db"}:
            continue
        try:
            entries.append(describe(item, base))
        except (FileNotFoundError, PermissionError):
            continue
    return sorted(entries, key=lambda x: (x["kind"] != "directory", x["name"].casefold()))


def ranged_file(path: Path, request: Request):
    size = path.stat().st_size
    range_header = request.headers.get("range")
    headers = {"Accept-Ranges": "bytes", "Content-Disposition": f'inline; filename="{path.name}"'}
    if not range_header:
        def full():
            with path.open("rb") as fh:
                while chunk := fh.read(1024 * 1024):
                    yield chunk
        headers["Content-Length"] = str(size)
        return StreamingResponse(full(), media_type=content_type(path), headers=headers)
    try:
        unit, value = range_header.split("=", 1)
        if unit != "bytes":
            raise ValueError
        start_text, end_text = value.split("-", 1)
        start = int(start_text) if start_text else 0
        end = min(int(end_text), size - 1) if end_text else size - 1
        if start < 0 or end < start or start >= size:
            raise ValueError
    except ValueError:
        raise HTTPException(416, "Invalid byte range", headers={"Content-Range": f"bytes */{size}"})
    length = end - start + 1
    def segment():
        with path.open("rb") as fh:
            fh.seek(start)
            remaining = length
            while remaining:
                chunk = fh.read(min(1024 * 1024, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
                yield chunk
    headers.update({"Content-Range": f"bytes {start}-{end}/{size}", "Content-Length": str(length)})
    return StreamingResponse(segment(), status_code=206, media_type=content_type(path), headers=headers)


def thumbnail(path: Path, cache_dir: Path, size: int = 480) -> Response:
    key = hashlib.sha256(f"{path}:{path.stat().st_mtime_ns}:{size}".encode()).hexdigest()
    target = cache_dir / "thumbs" / f"{key}.jpg"
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        with _THUMBNAIL_SLOTS:
            # Another request may have completed the same thumbnail while this
            # request was waiting for the single generation slot.
            if not target.exists():
                temp = target.with_suffix(".tmp.jpg")
                try:
                    if media_type(path) == "image":
                        with Image.open(path) as image:
                            image = ImageOps.exif_transpose(image).convert("RGB")
                            image.thumbnail((size, size))
                            image.save(temp, "JPEG", quality=82, optimize=True)
                    elif media_type(path) == "video":
                        subprocess.run(
                            ["ffmpeg", "-v", "error", "-ss", "00:00:15", "-i", str(path), "-frames:v", "1", "-vf", f"scale='min({size},iw)':-2", "-y", str(temp)],
                            check=True, timeout=90,
                        )
                    else:
                        raise HTTPException(404, "No preview")
                    os.replace(temp, target)
                except (OSError, subprocess.SubprocessError):
                    temp.unlink(missing_ok=True)
                    raise HTTPException(422, "Preview unavailable")
    return Response(target.read_bytes(), media_type="image/jpeg", headers={"Cache-Control": "public, max-age=86400"})


def subtitle_vtt(path: Path, cache_dir: Path) -> FileResponse:
    key = hashlib.sha256(f"subtitle-v2:{path}:{path.stat().st_mtime_ns}".encode()).hexdigest()
    target = cache_dir / "subtitles" / f"{key}.vtt"
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        stem = path.stem.casefold()
        suffixes = {".srt", ".ass", ".ssa", ".vtt"}
        sidecar = next(
            (item for item in path.parent.iterdir() if item.suffix.casefold() in suffixes and (item.stem.casefold() == stem or item.stem.casefold().startswith(stem + "."))),
            None,
        )
        temp = target.with_suffix(".tmp.vtt")
        input_path = sidecar or path
        normalized: Path | None = None
        if sidecar is not None:
            detected = from_bytes(sidecar.read_bytes()).best()
            if detected is None:
                raise HTTPException(422, "Subtitle character encoding could not be detected")
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", suffix=sidecar.suffix,
                prefix="subtitle-", dir=target.parent, delete=False,
            ) as handle:
                handle.write(str(detected))
                normalized = Path(handle.name)
            input_path = normalized
        command = ["ffmpeg", "-y", "-nostdin", "-v", "error", "-i", str(input_path)]
        if sidecar is None:
            command += ["-map", "0:s:0"]
        command += ["-f", "webvtt", str(temp)]
        try:
            subprocess.run(command, check=True, timeout=60)
            os.replace(temp, target)
        except (OSError, subprocess.SubprocessError):
            temp.unlink(missing_ok=True)
            raise HTTPException(404, "No compatible subtitle found")
        finally:
            if normalized is not None:
                normalized.unlink(missing_ok=True)
    return FileResponse(target, media_type="text/vtt", headers={"Cache-Control": "public, max-age=86400"})


def rename_item(source: Path, destination: Path) -> None:
    if destination.exists():
        raise FileExistsError(destination)
    source.rename(destination)


def delete_item(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink()
