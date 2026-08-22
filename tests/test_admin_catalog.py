from app.db import Database
from app import main


def test_relocate_catalog_preserves_metadata_for_tree(tmp_path, monkeypatch):
    database = Database(tmp_path / "catalog.sqlite3")
    database.initialize()
    monkeypatch.setattr(main, "db", database)
    with database.connect() as db:
        db.execute(
            "INSERT INTO items(library,rel_path,parent_path,name,kind,media_type,title,synopsis) "
            "VALUES('video','Series','', 'Series','directory','other','Curated series','Summary')"
        )
        db.execute(
            "INSERT INTO items(library,rel_path,parent_path,name,kind,media_type,title,synopsis) "
            "VALUES('video','Series/Episode.mkv','Series','Episode.mkv','file','video','Curated episode','Episode summary')"
        )
    main.relocate_catalog("video/Series", "video/Archive/Series")
    with database.connect() as db:
        rows = db.execute(
            "SELECT rel_path,parent_path,title,synopsis FROM items ORDER BY rel_path"
        ).fetchall()
    assert [row["rel_path"] for row in rows] == [
        "Archive/Series",
        "Archive/Series/Episode.mkv",
    ]
    assert rows[1]["parent_path"] == "Archive/Series"
    assert rows[1]["title"] == "Curated episode"
    assert rows[1]["synopsis"] == "Episode summary"
