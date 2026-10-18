# Architecture

Media Center is a FastAPI web application with a local SQLite catalog. It
browses three read-only libraries (`amintiri` = memories, `audio`, `video`),
offers guarded file management through a host helper, and hands video
playback to Jellyfin.

## Modules

| Layer | Module | Responsibility |
|---|---|---|
| Domain | `media_types` | File classification (image / audio / video / other) and which containers a browser plays as they are |
| Domain | `playback` | Playback policy per library (Strategy): direct stream or Jellyfin HLS |
| Domain | `episode_sync`, `media_metadata`, `imdb_sync` | Titles, episodes and metadata |
| Adapter: files | `files`, `scanner`, `security` | Directory listing, ranged streaming, thumbnails (ffmpeg / Pillow), archive downloads, catalog scan, path confinement |
| Adapter: database | `db`, `details_migration` | SQLite catalog |
| Adapter: Jellyfin | `jellyfin` | HLS session URLs, library refresh, read-only id reconciliation from Jellyfin's database |
| Adapter: host helper | `admin_client`, `admin_jobs` | Admin file operations and the follow-up index refresh |
| Web | `main`, `static/` | HTTP routes and the browser UI |

## Video playback

```
browser ──POST /api/playback/{library}──▶ PlaybackPlanner
                                         ├─ amintiri + .mp4/.m4v/.mov/.webm ─▶ {"engine":"direct", url:/api/media/…}
                                         └─ otherwise ─▶ catalog ids ─▶ Jellyfin master.m3u8 (copy or VA-API transcode)
```

- `video`: always Jellyfin.
- `amintiri`: direct when the browser can open the container, Jellyfin for
  `.3gp`, `.3g2`, `.mts`, `.vob` and other formats. Jellyfin indexes the
  memories folder as a separate home-videos library with photos, trickplay,
  chapter images, internet metadata and real-time monitoring turned off, so it
  only provides transcoding.
- `audio`: direct only.

Jellyfin item ids are copied into the catalog by `JellyfinClient.sync_catalog`
for every library in `playback.JELLYFIN_LIBRARIES`, joined on the full physical
path. Decision record: `decisions/0001-memories-video-playback.md`.
