from __future__ import annotations

from pathlib import Path
from urllib.parse import unquote


LIBRARIES = {"amintiri", "audio", "video"}


class UnsafePath(ValueError):
    pass


def _clean_relative(value: str) -> Path:
    value = unquote(value or "").replace("\\", "/")
    path = Path(value)
    if path.is_absolute() or ".." in path.parts or "\x00" in value:
        raise UnsafePath("Path escapes the media library")
    return path


def resolve_library(root: Path, library: str, relative: str = "", *, must_exist: bool = True) -> Path:
    if library not in LIBRARIES:
        raise UnsafePath("Unknown library")
    base = (root / library).resolve()
    candidate = (base / _clean_relative(relative)).resolve(strict=False)
    if candidate != base and base not in candidate.parents:
        raise UnsafePath("Path escapes the selected library")
    if must_exist and not candidate.exists():
        raise FileNotFoundError(candidate)
    return candidate

def resolve_admin(root: Path, relative: str = "", *, must_exist: bool = True) -> Path:
    base = root.resolve()
    candidate = (base / _clean_relative(relative)).resolve(strict=False)
    if candidate == base and relative:
        raise UnsafePath("Invalid path")
    if candidate != base and base not in candidate.parents:
        raise UnsafePath("Path escapes media root")
    if must_exist and not candidate.exists():
        raise FileNotFoundError(candidate)
    return candidate
