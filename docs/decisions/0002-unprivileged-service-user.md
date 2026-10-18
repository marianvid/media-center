# 0002 — Media Center runs as an unprivileged user

Date: 2026-10-19 · Status: accepted

## Context

`media-center.service` ran as root although it only listens on a port above
1024, reads the media libraries and writes its own state and cache. File
changes on the media disk are done by the separate host helper
(`deploy/media-admin.py`) through a Unix socket that was writable by every
local user (mode 666).

## Decision

- The web application runs as system user `media-center` (fixed uid/gid,
  no login shell, no capabilities, `ProtectHome`, `RestrictSUIDSGID`).
- It owns only `/var/lib/media-center` and `/var/cache/media-center`; the code
  stays owned by root and read-only to the service. The Jellyfin env file
  stays root-only: systemd reads `EnvironmentFile=` before dropping privileges.
- Media libraries the user cannot read through "other" permissions get a
  read-only ACL for its uid on the storage host.
- The host helper keeps root (it must write the root-owned media disk) and
  accepts `MEDIA_ADMIN_SOCKET_GID`: the socket becomes mode 660 for that group
  only. Without the variable it keeps mode 666.

## Consequences

A compromised web process can no longer change the container or read root's
secrets, and only the service user can ask the helper for file operations.
Deployments must keep `/opt/media-center` owned by root.
