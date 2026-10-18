# 0003 — Indexing only on demand, per folder

Date: 2026-10-19 · Status: accepted

## Context

The media HDD is meant to sleep. Media Center scanned every library when its
service started, and Jellyfin's scheduled tasks (library scan and media
segment scan every 12 h, audio normalisation, chapter and trickplay images,
subtitle, lyric and people downloads) read the disk on their own. A full
Jellyfin scan of the new Memories library kept the disk busy for hours.

## Decision

- No automatic indexing. Media Center does not scan at start
  (`MEDIA_SCAN_ON_START=1` restores it). `scripts/configure_jellyfin_quiet.py`
  removes the schedules of Jellyfin's disk-reading tasks, saving them for
  `--restore`. Jellyfin's real-time monitoring stays off.
- The Refresh button of each section refreshes the folder on screen and
  everything below it; at the section root, the whole library.
  `FolderRefresher` runs, in order: catalog scan of that subtree (stale rows
  removed only inside it), Jellyfin refresh of the same folder plus id
  reconciliation, and pre-generation of photo and video previews.
- Admin file operations keep their targeted refresh: they are user actions.

## Consequences

- Files copied to the disk outside Media Center (for example over Samba)
  appear only after a Refresh of their folder.
- Browsing still reads directory listings from the disk, by design.
- A Refresh of a whole large library can take hours (previews); progress is
  shown on screen and only one refresh runs at a time.
