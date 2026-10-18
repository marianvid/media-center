# 0001 — Memories videos in browser-incompatible formats play through Jellyfin

Date: 2026-10-18 · Status: accepted

## Context

Memories (`amintiri`) holds old phone clips (`.3gp`: MPEG-4 Part 2 + AAC),
camcorder clips (`.mts`: interlaced H.264 + AC-3 in MPEG-TS) and DVD rips
(`.vob`). Their extensions were not classified as video, so they appeared as
"other" without thumbnails. Even classified as video, browsers cannot decode
these containers or codecs, and the memories library was played only by a
direct `<video src>` stream.

## Decision

1. Classify `.3gp`, `.3g2`, `.mts`, `.vob` as video.
2. Introduce `PlaybackPlanner` (Strategy per library). In `amintiri`, files
   whose container every browser opens (`.mp4`, `.m4v`, `.mov`, `.webm`) keep
   direct playback; all other videos use the same Jellyfin HLS path as the
   video library.
3. Jellyfin indexes the memories folder as a separate, read-only home-videos
   library with photos, trickplay, chapter images, internet providers and
   real-time monitoring disabled. `sync_catalog` maps ids for every Jellyfin
   library.
4. Video thumbnails fall back from 15 s to 1 s to the first frame, because
   phone clips are often shorter than 15 s.

## Alternatives rejected

- Converting the files to MP4 copies: the media mounts are read-only by design
  and copies double storage.
- An on-the-fly ffmpeg pipeline inside Media Center: a custom transcoding
  engine was retired in favour of Jellyfin; reviving it duplicates Jellyfin.
- Routing every memories video through Jellyfin: adds transcoding load and
  start-up delay for files the browser already plays.

## Consequences

- One more Jellyfin library; its first scan reads the memories folder once.
- `.mov` files with codecs a browser cannot decode still play directly and may
  fail; they can move to the Jellyfin path later by changing
  `BROWSER_VIDEO_EXT`.
