from __future__ import annotations

import json
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .admin_client import AdminClient, AdminError
from .admin_jobs import AdminIndexer
from .config import settings
from .db import Database
from .files import list_directory, ranged_file, subtitle_vtt, thumbnail
from .episode_sync import episode_label
from .imdb_sync import ImdbSync
from .jellyfin import JellyfinClient, JellyfinError
from .scanner import CatalogScanner
from .security import UnsafePath, resolve_admin, resolve_library


db = Database(settings.database)
scanner = CatalogScanner(db, settings.readonly_root)
imdb = ImdbSync(db, settings.imdb_cache_dir)
admin = AdminClient(settings.admin_socket)
jellyfin = JellyfinClient(
    settings.jellyfin_url,
    settings.jellyfin_public_url,
    settings.jellyfin_token,
    settings.jellyfin_user_id,
    settings.jellyfin_admin_token,
)
admin_indexer = AdminIndexer(db, scanner, jellyfin, settings.readonly_root, settings.jellyfin_database)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings.state_dir.mkdir(parents=True, exist_ok=True)
    settings.cache_dir.mkdir(parents=True, exist_ok=True)
    db.initialize()
    scanner.start()
    yield


app = FastAPI(title="Home Media Center", lifespan=lifespan)


def decorate_episode(item: dict) -> dict:
    if item.get("imdb_match_status") == "manual_episode":
        item["episode_label"] = episode_label(item.get("path", ""))
    return item


@app.exception_handler(UnsafePath)
async def unsafe_path_handler(_request, exc):
    return JSONResponse({"detail": str(exc)}, status_code=400)


@app.get("/api/library/{library}")
def browse_library(library: str, path: str = ""):
    target = resolve_library(settings.readonly_root, library, path)
    if not target.is_dir():
        raise HTTPException(400, "Not a directory")
    base = (settings.readonly_root / library).resolve()
    items = list_directory(target, base)
    if library == "video":
        # Companion artwork, subtitles and metadata remain available to the
        # player, but the video library itself only presents folders and films.
        items = [item for item in items if item["kind"] == "directory" or item["media_type"] == "video"]
    if library in {"amintiri", "audio", "video"}:
        with db.connect() as conn:
            metadata = {
                row["rel_path"]: dict(row)
                for row in conn.execute(
                    "SELECT rel_path,title,year,imdb_id,imdb_rating,genres,actors,imdb_match_status FROM items "
                    "WHERE library=? AND parent_path=?",
                    (library, path),
                )
            }
            if library == "amintiri":
                for item in items:
                    if item["kind"] != "directory":
                        continue
                    escaped_path = (
                        item["path"].replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
                    )
                    item["file_count"] = conn.execute(
                        "SELECT count(*) FROM items "
                        "WHERE library='amintiri' AND kind='file' AND rel_path LIKE ? ESCAPE '\\'",
                        (f"{escaped_path}/%",),
                    ).fetchone()[0]
        for item in items:
            item.update(metadata.get(item["path"], {}))
            decorate_episode(item)
    return {"library": library, "path": path, "items": items}


@app.get("/api/media/{library}")
def stream_media(request: Request, library: str, path: str = Query(...)):
    target = resolve_library(settings.readonly_root, library, path)
    if not target.is_file():
        raise HTTPException(400, "Not a file")
    return ranged_file(target, request)


@app.get("/api/thumbnail/{library}")
def media_thumbnail(library: str, path: str = Query(...), size: int = Query(480, ge=96, le=1200)):
    target = resolve_library(settings.readonly_root, library, path)
    return thumbnail(target, settings.cache_dir, size)


@app.get("/api/subtitle/{library}")
def media_subtitle(library: str, path: str = Query(...)):
    target = resolve_library(settings.readonly_root, library, path)
    if not target.is_file():
        raise HTTPException(400, "Not a file")
    return subtitle_vtt(target, settings.cache_dir)


@app.post("/api/playback/{library}")
def start_playback(library: str, path: str = Query(...)):
    if library != "video":
        raise HTTPException(400, "Jellyfin playback is only available for the video library")
    target = resolve_library(settings.readonly_root, library, path)
    if not target.is_file():
        raise HTTPException(400, "Not a file")
    with db.connect() as conn:
        mapping = conn.execute(
            "SELECT jellyfin_item_id,jellyfin_media_source_id FROM items "
            "WHERE library='video' AND media_type='video' AND rel_path=?",
            (path,),
        ).fetchone()
    if mapping is None or not mapping["jellyfin_item_id"] or not mapping["jellyfin_media_source_id"]:
        raise HTTPException(503, "Video is not mapped to Jellyfin yet")
    try:
        return jellyfin.playback(mapping["jellyfin_item_id"], mapping["jellyfin_media_source_id"])
    except JellyfinError as exc:
        raise HTTPException(503, str(exc))


@app.post("/api/jobs/jellyfin-sync")
def sync_jellyfin_catalog():
    try:
        return jellyfin.sync_catalog(
            db,
            settings.readonly_root / "video",
            settings.jellyfin_database,
        )
    except JellyfinError as exc:
        raise HTTPException(503, str(exc))


@app.get("/api/search")
def search(q: str = "", library: str = "video", media: str = "", genre: str = "", actor: str = "", year: int | None = None, decade: int | None = None, group: str = "folder", limit: int = Query(500, le=2000)):
    clauses = ["library=?"]
    params: list = [library]
    if library == "video":
        clauses.append("(kind='directory' OR media_type='video')")
    if media:
        clauses.append("media_type=?")
        params.append(media)
    if genre:
        clauses.append("genres LIKE ?")
        params.append(f"%{genre}%")
    if actor:
        clauses.append("actors LIKE ?")
        params.append(f"%{actor}%")
    if decade is not None:
        clauses.append("year>=? AND year<?")
        params.extend((decade, decade + 10))
    if year is not None:
        clauses.append("year=?")
        params.append(year)
    if library in {"amintiri", "audio"} and (genre or year is not None):
        clauses.append("media_type=?")
        params.append("image" if library == "amintiri" else "audio")
    if q:
        clauses.append("id IN (SELECT rowid FROM item_search WHERE item_search MATCH ?)")
        params.append('"' + q.replace('"', '""') + '"*')
    if library == "video" and group != "folder":
        clauses.append("imdb_id IS NOT NULL")
    params.append(limit)
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT id,library,rel_path AS path,parent_path,name,kind,media_type,size,mtime,title,year,imdb_id,imdb_rating,genres,actors,imdb_match_status "
            f"FROM items WHERE {' AND '.join(clauses)} ORDER BY CASE kind WHEN 'directory' THEN 0 ELSE 1 END,name COLLATE NOCASE LIMIT ?", params
        ).fetchall()
    items = [decorate_episode(dict(row)) for row in rows]
    if group != "folder":
        unique = {}
        for item in items:
            unique.setdefault(item["imdb_id"], item)
        items = list(unique.values())
    return {"group": group, "items": items}


@app.get("/api/filters/{library}")
def library_filters(library: str):
    media = {"amintiri": "image", "audio": "audio"}.get(library)
    if not media:
        raise HTTPException(404, "Filters are not available for this library")
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT year,genres FROM items WHERE library=? AND media_type=?",
            (library, media),
        ).fetchall()
    years: dict[int, int] = {}
    genres: dict[str, int] = {}
    for row in rows:
        if row["year"]:
            years[row["year"]] = years.get(row["year"], 0) + 1
        for genre in filter(None, (value.strip() for value in row["genres"].split(","))):
            genres[genre] = genres.get(genre, 0) + 1
    return {
        "years": [
            {"value": value, "label": str(value), "count": count}
            for value, count in sorted(years.items(), reverse=True)
        ],
        "genres": [
            {"value": value, "count": count}
            for value, count in sorted(genres.items(), key=lambda item: (-item[1], item[0].casefold()))
        ],
    }


@app.get("/api/video/filters")
def video_filters():
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT imdb_id,rel_path,year,genres FROM items "
            "WHERE library='video' AND imdb_id IS NOT NULL AND (kind='directory' OR media_type='video')"
        ).fetchall()
    unique = {}
    for row in rows:
        unique.setdefault(row["imdb_id"] or row["rel_path"], row)
    genre_counts: dict[str, int] = {}
    decade_counts: dict[int, int] = {}
    for row in unique.values():
        for genre in filter(None, (value.strip() for value in row["genres"].split(","))):
            genre_counts[genre] = genre_counts.get(genre, 0) + 1
        if row["year"]:
            decade = row["year"] // 10 * 10
            decade_counts[decade] = decade_counts.get(decade, 0) + 1
    return {
        "genres": [
            {"value": value, "count": count}
            for value, count in sorted(genre_counts.items(), key=lambda item: (-item[1], item[0].casefold()))
        ],
        "decades": [
            {"value": value, "label": f"{value}s", "count": count}
            for value, count in sorted(decade_counts.items(), reverse=True)
        ],
    }


@app.get("/api/admin")
def browse_admin(path: str = ""):
    target = resolve_admin(settings.manage_root, path)
    if not target.is_dir():
        raise HTTPException(400, "Not a directory")
    return {"path": path, "items": list_directory(target, settings.manage_root.resolve())}


def relocate_catalog(old_path: str, new_path: str) -> None:
    old_parts, new_parts = Path(old_path).parts, Path(new_path).parts
    if len(old_parts) < 2 or len(new_parts) < 2:
        return
    old_library, new_library = old_parts[0], new_parts[0]
    if old_library not in {"amintiri", "audio", "video"} or new_library not in {"amintiri", "audio", "video"}:
        return
    old_relative = Path(*old_parts[1:]).as_posix()
    new_relative = Path(*new_parts[1:]).as_posix()
    escaped = old_relative.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT id,rel_path FROM items WHERE library=? AND (rel_path=? OR rel_path LIKE ? ESCAPE '\\') "
            "ORDER BY length(rel_path)",
            (old_library, old_relative, f"{escaped}/%"),
        ).fetchall()
        for row in rows:
            suffix = row["rel_path"][len(old_relative):]
            relocated = new_relative + suffix
            parent = Path(relocated).parent.as_posix()
            if parent == ".":
                parent = ""
            conn.execute(
                "UPDATE items SET library=?,rel_path=?,parent_path=?,name=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (new_library, relocated, parent, Path(relocated).name, row["id"]),
            )


class RenameRequest(BaseModel):
    path: str
    new_name: str = Field(min_length=1, max_length=255)


@app.post("/api/admin/rename")
def admin_rename(payload: RenameRequest):
    source = resolve_admin(settings.manage_root, payload.path)
    if source.parent == settings.manage_root.resolve() and source.name in {"amintiri", "audio", "video"}:
        raise HTTPException(400, "Library roots cannot be renamed")
    if "/" in payload.new_name or "\\" in payload.new_name or payload.new_name in {".", ".."}:
        raise HTTPException(400, "Invalid name")
    try:
        result = admin.request({"action": "rename", "path": payload.path, "new_name": payload.new_name})
    except AdminError as exc:
        raise HTTPException(409, str(exc))
    relocate_catalog(payload.path, result["path"])
    admin_indexer.schedule(payload.path, result["path"])
    return result


class CreateDirectoryRequest(BaseModel):
    path: str = ""
    name: str = Field(min_length=1, max_length=255)


@app.post("/api/admin/directory")
def admin_create_directory(payload: CreateDirectoryRequest):
    resolve_admin(settings.manage_root, payload.path)
    if "/" in payload.name or "\\" in payload.name or payload.name in {".", ".."}:
        raise HTTPException(400, "Invalid name")
    try:
        result = admin.request({"action": "mkdir", "path": payload.path, "name": payload.name})
    except AdminError as exc:
        raise HTTPException(409, str(exc))
    admin_indexer.schedule(result["path"])
    return result


class MoveRequest(BaseModel):
    path: str
    destination: str = ""


@app.post("/api/admin/move")
def admin_move(payload: MoveRequest):
    source = resolve_admin(settings.manage_root, payload.path)
    destination = resolve_admin(settings.manage_root, payload.destination)
    if not destination.is_dir() or source == settings.manage_root.resolve() or (
        source.parent == settings.manage_root.resolve() and source.name in {"amintiri", "audio", "video"}
    ):
        raise HTTPException(400, "Invalid move")
    try:
        result = admin.request(
            {"action": "move", "path": payload.path, "destination": payload.destination}
        )
    except AdminError as exc:
        raise HTTPException(409, str(exc))
    relocate_catalog(payload.path, result["path"])
    admin_indexer.schedule(payload.path, result["path"])
    return result


@app.put("/api/admin/upload")
async def admin_upload(request: Request, path: str = "", name: str = Query(..., min_length=1, max_length=255)):
    destination = resolve_admin(settings.manage_root, path)
    if not destination.is_dir() or "/" in name or "\\" in name or name in {".", ".."}:
        raise HTTPException(400, "Invalid upload destination")
    length_text = request.headers.get("content-length")
    try:
        length = int(length_text or "")
    except ValueError:
        raise HTTPException(411, "Upload size is required")
    if length < 0:
        raise HTTPException(400, "Invalid upload size")
    try:
        result = await admin.upload(
            {"action": "upload", "path": path, "name": name, "size": length},
            request.stream(),
        )
    except AdminError as exc:
        raise HTTPException(409, str(exc))
    admin_indexer.schedule(path, result["path"])
    return result


class DeleteRequest(BaseModel):
    path: str
    confirm_name: str


@app.post("/api/admin/delete")
def admin_delete(payload: DeleteRequest):
    target = resolve_admin(settings.manage_root, payload.path)
    if target == settings.manage_root.resolve() or (
        target.parent == settings.manage_root.resolve() and target.name in {"amintiri", "audio", "video"}
    ):
        raise HTTPException(400, "Cannot delete a media library root")
    if payload.confirm_name != target.name:
        raise HTTPException(400, "Confirmation does not match")
    try:
        admin.request({"action": "delete", "path": payload.path, "confirm_name": payload.confirm_name})
    except AdminError as exc:
        raise HTTPException(409, str(exc))
    parent = Path(payload.path).parent.as_posix()
    admin_indexer.schedule("" if parent == "." else parent)
    return {"ok": True}


@app.post("/api/admin/refresh")
def admin_refresh_indexes():
    return {"started": admin_indexer.schedule(full=True)}


def admin_catalog_path(path: str) -> tuple[str, str]:
    target = resolve_admin(settings.manage_root, path)
    relative = target.relative_to(settings.manage_root.resolve())
    if len(relative.parts) < 2 or relative.parts[0] not in {"amintiri", "audio", "video"}:
        raise HTTPException(400, "Select a media item inside a library")
    return relative.parts[0], Path(*relative.parts[1:]).as_posix()


@app.get("/api/admin/metadata")
def admin_metadata(path: str = Query(...)):
    library, relative = admin_catalog_path(path)
    with db.connect() as conn:
        row = conn.execute(
            "SELECT library,rel_path,name,kind,media_type,title,year,imdb_id,imdb_rating,genres,actors,synopsis "
            "FROM items WHERE library=? AND rel_path=?",
            (library, relative),
        ).fetchone()
    if row is None:
        raise HTTPException(404, "Item is not indexed yet")
    return dict(row)


class MetadataRequest(BaseModel):
    path: str
    title: str = Field(default="", max_length=500)
    year: int | None = Field(default=None, ge=1800, le=2200)
    imdb_id: str = Field(default="", max_length=20)
    imdb_rating: float | None = Field(default=None, ge=0, le=10)
    genres: str = Field(default="", max_length=1000)
    actors: str = Field(default="", max_length=5000)
    synopsis: str = Field(default="", max_length=50000)


@app.put("/api/admin/metadata")
def admin_update_metadata(payload: MetadataRequest):
    library, relative = admin_catalog_path(payload.path)
    imdb_id = payload.imdb_id.strip()
    if imdb_id and (not imdb_id.startswith("tt") or not imdb_id[2:].isdigit()):
        raise HTTPException(400, "IMDb ID must look like tt1234567")
    with db.connect() as conn:
        existing = conn.execute(
            "SELECT imdb_match_status FROM items WHERE library=? AND rel_path=?",
            (library, relative),
        ).fetchone()
        if existing is None:
            raise HTTPException(404, "Item is not indexed yet")
        status = existing["imdb_match_status"]
        if not status.startswith("manual"):
            status = "manual_admin"
        conn.execute(
            "UPDATE items SET title=?,year=?,imdb_id=?,imdb_rating=?,genres=?,actors=?,synopsis=?,"
            "imdb_match_status=?,updated_at=CURRENT_TIMESTAMP WHERE library=? AND rel_path=?",
            (
                payload.title.strip(), payload.year, imdb_id, payload.imdb_rating,
                payload.genres.strip(), payload.actors.strip(), payload.synopsis.strip(),
                status, library, relative,
            ),
        )
    return {"ok": True}


@app.post("/api/jobs/scan")
def start_scan():
    return {"started": scanner.start()}


@app.post("/api/jobs/imdb")
def start_imdb():
    return {"started": imdb.start()}


@app.get("/api/details/{imdb_id}")
def stored_details(imdb_id: str):
    if not imdb_id.startswith("tt") or not imdb_id[2:].isdigit():
        raise HTTPException(400, "Invalid IMDb ID")
    with db.connect() as conn:
        row = conn.execute(
            "SELECT imdb_id,imdb_match_status,synopsis,synopsis_language,synopsis_source,synopsis_retrieved_at "
            "FROM items WHERE library='video' AND imdb_id=? "
            "ORDER BY CASE WHEN synopsis<>'' THEN 0 ELSE 1 END LIMIT 1",
            (imdb_id,),
        ).fetchone()
    if not row:
        raise HTTPException(404, "Stored details not found")
    return dict(row)


@app.get("/api/jobs")
def jobs():
    with db.connect() as conn:
        rows = conn.execute("SELECT * FROM jobs ORDER BY name").fetchall()
    return [dict(row) for row in rows]


@app.get("/api/info")
def info():
    try:
        health = json.loads(settings.health_file.read_text())
    except (OSError, json.JSONDecodeError):
        health = {"available": False}
    stat = os.statvfs(settings.manage_root)
    health["filesystem"] = {
        "total": stat.f_blocks * stat.f_frsize,
        "available": stat.f_bavail * stat.f_frsize,
        "used": (stat.f_blocks - stat.f_bfree) * stat.f_frsize,
    }
    with db.connect() as conn:
        health["catalog"] = dict(conn.execute(
            "SELECT count(*) AS items,sum(CASE WHEN media_type='video' THEN 1 ELSE 0 END) AS videos,"
            "sum(CASE WHEN media_type='audio' THEN 1 ELSE 0 END) AS audio,"
            "sum(CASE WHEN media_type='image' THEN 1 ELSE 0 END) AS images FROM items"
        ).fetchone())
    return health


static_dir = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=static_dir), name="static")


@app.get("/{path:path}")
def frontend(path: str = ""):
    return FileResponse(static_dir / "index.html")
