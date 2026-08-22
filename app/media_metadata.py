from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from mutagen import File as MutagenFile, MutagenError


YEAR = re.compile(r"(?<!\d)(19\d{2}|20\d{2})(?!\d)")
EMPTY_GENRES = {"", "genre", "other", "unknown", "unknown genre", "n/a", "none"}


def path_year(rel_path: str, mtime: float, tagged_year: str = "") -> int:
    tagged = YEAR.search(tagged_year or "")
    if tagged:
        return int(tagged.group(1))
    candidates = [int(value) for value in YEAR.findall(rel_path)]
    if candidates:
        return candidates[-1]
    return datetime.fromtimestamp(mtime).year


def image_year(rel_path: str, mtime: float, parsed_year: int | None = None) -> int:
    if parsed_year:
        return parsed_year
    folder_years = [
        int(value)
        for component in Path(rel_path).parts[:-1]
        for value in YEAR.findall(component)
    ]
    return folder_years[-1] if folder_years else datetime.fromtimestamp(mtime).year


def audio_genres(rel_path: str, tagged_genres: list[str]) -> str:
    top = rel_path.split("/", 1)[0].casefold().replace("_", " ")
    forced = None
    if top in {"muzica clasica", "simfonica clasica"}:
        forced = "Classical"
    elif top in {"audio religioase", "filocaliile audio", "muzica psaltica"} or "pasti" in top:
        forced = "Religious"
    elif top.startswith("colinde"):
        forced = "Christmas"
    elif top == "povesti":
        forced = "Stories"
    elif top == "teatruradiofonic":
        forced = "Radio Theatre"
    elif top in {"neagu djuvara", "documentare arta"}:
        forced = "Spoken Word"
    elif top in {"muzica bebe", "cantecele pasti pt copii"}:
        forced = "Children"
    elif top in {"muzica nunta", "selecttii muzica botez ioan"}:
        forced = "Wedding"

    cleaned: list[str] = []
    for raw in tagged_genres:
        for value in re.split(r"[,;/]", raw or ""):
            value = value.strip()
            if value.casefold() not in EMPTY_GENRES and value not in cleaned:
                cleaned.append(value)
    if forced in {"Classical", "Religious", "Christmas", "Stories", "Radio Theatre", "Spoken Word", "Children"}:
        return forced
    if forced and forced not in cleaned:
        cleaned.append(forced)
    if not cleaned and top == "muzica diverse":
        cleaned.append("Various")
    return ",".join(cleaned)


def _values(tags, *names: str) -> list[str]:
    for name in names:
        values = tags.get(name) if tags else None
        if values:
            return [str(value) for value in values]
    return []


def read_audio_metadata(path: Path, rel_path: str, mtime: float) -> tuple[int, str, str]:
    tags = {}
    try:
        media = MutagenFile(path, easy=True)
        tags = media.tags if media and media.tags else {}
    except (OSError, ValueError, MutagenError):
        pass
    year = path_year(rel_path, mtime, " ".join(_values(tags, "date", "year", "originaldate")))
    genres = audio_genres(rel_path, _values(tags, "genre"))
    artists = ", ".join(_values(tags, "artist", "albumartist"))
    return year, genres, artists
