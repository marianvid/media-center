#!/usr/bin/env python3
"""Create or update the Jellyfin library that transcodes Memories videos.

Jellyfin only provides playback for this library (see
docs/decisions/0001-memories-video-playback.md): the options are copied from
the existing video library, then photos, trickplay, chapter images, internet
metadata and real-time monitoring are turned off. Safe to run again.
"""
from __future__ import annotations

import argparse
from pathlib import Path
from urllib.parse import urlencode

from scripts.configure_jellyfin_transcoding import load_env, request

QUIET_OPTIONS = {
    "EnablePhotos": False,
    "EnableRealtimeMonitor": False,
    "EnableTrickplayImageExtraction": False,
    "ExtractTrickplayImagesDuringLibraryScan": False,
    "EnableChapterImageExtraction": False,
    "ExtractChapterImagesDuringLibraryScan": False,
    "EnableInternetProviders": False,
    "SaveLocalMetadata": False,
    "AutomaticRefreshIntervalDays": 0,
}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--name", default="Memories")
    parser.add_argument("--path", type=Path, required=True, help="memories folder as Jellyfin sees it")
    parser.add_argument("--template", default="Video", help="existing library whose options are copied")
    parser.add_argument("--env", type=Path, default=Path("/etc/media-center/jellyfin.env"))
    args = parser.parse_args()

    env = load_env(args.env)
    url, token = env["JELLYFIN_URL"], env["JELLYFIN_ADMIN_TOKEN"]
    folders = {folder["Name"]: folder for folder in request(url, token, "/Library/VirtualFolders")}
    if args.template not in folders:
        parser.error(f"template library {args.template!r} does not exist")
    options = dict(folders[args.template].get("LibraryOptions") or {})
    options.update(QUIET_OPTIONS)
    options["PathInfos"] = [{"Path": str(args.path)}]

    if args.name in folders:
        options = {**folders[args.name].get("LibraryOptions", {}), **QUIET_OPTIONS}
        request(url, token, "/Library/VirtualFolders/LibraryOptions", method="POST",
                body={"Id": folders[args.name]["ItemId"], "LibraryOptions": options})
        print(f"updated library {args.name!r}")
        return
    query = urlencode({"name": args.name, "collectionType": "homevideos", "refreshLibrary": "true"})
    request(url, token, f"/Library/VirtualFolders?{query}", method="POST", body={"LibraryOptions": options})
    print(f"created library {args.name!r} at {args.path}; Jellyfin is scanning it")


if __name__ == "__main__":
    main()
