from __future__ import annotations

import html
import re
import sqlite3
from pathlib import Path


PARAGRAPH = re.compile(r"<p>(.*?)</p>", re.DOTALL)
LANGUAGE = re.compile(r'<html lang="([^"]+)"')
SOURCE = re.compile(r'<footer>Source: <a href="([^"]+)"')
IMPORTED = re.compile(r"Imported ([^<.]+(?:\.[^<.]+)?\+00:00)\.")


def parse_page(path: Path) -> tuple[str, str, str, str] | None:
    document = path.read_text(encoding="utf-8")
    paragraph = PARAGRAPH.search(document)
    if not paragraph:
        return None
    synopsis = html.unescape(paragraph.group(1)).strip()
    if not synopsis or synopsis.startswith("No Wikipedia synopsis was found"):
        return None
    language = LANGUAGE.search(document)
    source = SOURCE.search(document)
    imported = IMPORTED.search(document)
    return (
        synopsis,
        html.unescape(language.group(1)) if language else "",
        html.unescape(source.group(1)) if source else "",
        html.unescape(imported.group(1)) if imported else "",
    )


def migrate(database: Path, details_dir: Path) -> tuple[int, int]:
    connection = sqlite3.connect(database, timeout=120)
    connection.execute("PRAGMA busy_timeout=120000")
    parsed = 0
    updated = 0
    try:
        for folder in sorted(details_dir.glob("tt*")):
            files = list(folder.glob("*.html"))
            if not files:
                continue
            details = parse_page(files[0])
            if not details:
                continue
            parsed += 1
            cursor = connection.execute(
                "UPDATE items SET synopsis=?,synopsis_language=?,synopsis_source=?,synopsis_retrieved_at=? "
                "WHERE library='video' AND imdb_id=?",
                (*details, folder.name),
            )
            if cursor.rowcount:
                updated += 1
        connection.commit()
    finally:
        connection.close()
    return parsed, updated
