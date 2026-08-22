from pathlib import Path

from app.db import Database
from app.details_migration import migrate


def test_html_details_are_migrated_to_every_matching_item(tmp_path: Path):
    database = tmp_path / "catalog.db"
    db = Database(database)
    db.initialize()
    with db.connect() as conn:
        for rel_path in ("Alien.mkv", "duplicates/Alien.mkv"):
            conn.execute(
                "INSERT INTO items(library,rel_path,parent_path,name,kind,media_type,title,imdb_id) "
                "VALUES('video',?,?,?,'file','video','Alien','tt0078748')",
                (rel_path, str(Path(rel_path).parent).replace(".", ""), Path(rel_path).name),
            )

    folder = tmp_path / "details" / "tt0078748"
    folder.mkdir(parents=True)
    (folder / "Alien (1979).html").write_text(
        '<html lang="en"><body><p>A crew encounters a hostile creature.</p>'
        '<footer>Source: <a href="https://en.wikipedia.org/wiki/Alien_(film)">Wikipedia</a>. '
        'Imported 2026-08-16T17:41:26.459064+00:00.</footer></body></html>',
        encoding="utf-8",
    )

    parsed, updated = migrate(database, tmp_path / "details")

    assert (parsed, updated) == (1, 1)
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT synopsis,synopsis_language,synopsis_source,synopsis_retrieved_at FROM items ORDER BY rel_path"
        ).fetchall()
    assert len(rows) == 2
    assert all(row["synopsis"] == "A crew encounters a hostile creature." for row in rows)
    assert all(row["synopsis_language"] == "en" for row in rows)
    assert all(row["synopsis_source"].startswith("https://en.wikipedia.org/") for row in rows)
    assert all(row["synopsis_retrieved_at"].startswith("2026-08-16") for row in rows)


def test_placeholder_file_does_not_become_a_synopsis(tmp_path: Path):
    database = tmp_path / "catalog.db"
    db = Database(database)
    db.initialize()
    folder = tmp_path / "details" / "tt1234567"
    folder.mkdir(parents=True)
    (folder / "Rare.html").write_text(
        '<html lang="en"><body><p>No Wikipedia synopsis was found for this title.</p></body></html>',
        encoding="utf-8",
    )

    assert migrate(database, tmp_path / "details") == (0, 0)
