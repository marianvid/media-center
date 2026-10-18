#!/usr/bin/env python3
"""Remove the schedules of Jellyfin tasks that read the media disk.

The media HDD should sleep until the user presses Refresh in Media Center
(docs/decisions/0003-refresh-on-demand.md). Maintenance tasks that work only
on Jellyfin's own data (cache, logs, database) keep their schedules.
The previous schedules are saved once to ``--backup``; ``--restore`` puts
them back. Safe to run again.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from scripts.configure_jellyfin_transcoding import load_env, request

# Task keys as Jellyfin names them; display names differ by language.
DISK_TASKS = {
    "RefreshLibrary",            # Scan Media Library
    "TaskExtractMediaSegments",  # Media Segment Scan
    "AudioNormalization",        # Audio Normalization
    "RefreshChapterImages",      # Extract Chapter Images
    "RefreshTrickplayImages",    # Generate Trickplay Images
    "DownloadSubtitles",         # Download missing subtitles
    "RefreshLyrics",             # Download missing lyrics
    "RefreshPeople",             # Refresh People
}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--env", type=Path, default=Path("/etc/media-center/jellyfin.env"))
    parser.add_argument("--backup", type=Path, default=Path("/var/lib/media-center/jellyfin-task-triggers.json"))
    parser.add_argument("--restore", action="store_true")
    args = parser.parse_args()

    env = load_env(args.env)
    url, token = env["JELLYFIN_URL"], env["JELLYFIN_ADMIN_TOKEN"]
    tasks = request(url, token, "/ScheduledTasks")
    if args.restore:
        saved = json.loads(args.backup.read_text())
        for task in tasks:
            if task["Key"] in saved:
                request(url, token, f"/ScheduledTasks/{task['Id']}/Triggers", method="POST", body=saved[task["Key"]])
                print(f"restored {task['Name']}")
        return

    selected = [task for task in tasks if task["Key"] in DISK_TASKS]
    if not args.backup.exists():
        args.backup.write_text(json.dumps({task["Key"]: task.get("Triggers", []) for task in selected}, indent=2))
        print(f"saved previous schedules to {args.backup}")
    for task in selected:
        if task.get("Triggers"):
            request(url, token, f"/ScheduledTasks/{task['Id']}/Triggers", method="POST", body=[])
            print(f"unscheduled {task['Name']} ({task['Key']})")
    missing = DISK_TASKS - {task["Key"] for task in selected}
    if missing:
        print("not found (check names for this Jellyfin version): " + ", ".join(sorted(missing)))


if __name__ == "__main__":
    main()
