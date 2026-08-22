#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shlex
import urllib.request
from pathlib import Path


def load_env(path: Path) -> dict[str, str]:
    values = {}
    for line in path.read_text().splitlines():
        key, separator, value = line.strip().partition("=")
        if separator:
            values[key] = shlex.split(value)[0] if value else ""
    return values


def request(base_url: str, token: str, path: str, *, method: str = "GET", body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        base_url.rstrip("/") + path,
        data=data,
        method=method,
        headers={"X-Emby-Token": token, "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=120) as response:
        content = response.read()
        return json.loads(content) if content else None


def main() -> None:
    parser = argparse.ArgumentParser(description="Configure bounded Jellyfin HLS transcoding")
    parser.add_argument("--segment-keep-seconds", type=int, default=300)
    parser.add_argument("--throttle-delay-seconds", type=int, default=60)
    parser.add_argument("--env", type=Path, default=Path("/etc/media-center/jellyfin.env"))
    args = parser.parse_args()
    if args.segment_keep_seconds < 15 or args.throttle_delay_seconds < 0:
        parser.error("segment window must be at least 15 seconds and throttle delay cannot be negative")

    env = load_env(args.env)
    encoding = request(env["JELLYFIN_URL"], env["JELLYFIN_ADMIN_TOKEN"], "/System/Configuration/encoding")
    encoding.update(
        {
            "TranscodingTempPath": "/var/cache/jellyfin/transcodes",
            "EnableThrottling": True,
            "ThrottleDelaySeconds": args.throttle_delay_seconds,
            "EnableSegmentDeletion": True,
            "SegmentKeepSeconds": args.segment_keep_seconds,
        }
    )
    request(
        env["JELLYFIN_URL"],
        env["JELLYFIN_ADMIN_TOKEN"],
        "/System/Configuration/encoding",
        method="POST",
        body=encoding,
    )
    print(
        json.dumps(
            {
                "transcoding_temp_path": encoding["TranscodingTempPath"],
                "throttling": encoding["EnableThrottling"],
                "throttle_delay_seconds": encoding["ThrottleDelaySeconds"],
                "segment_deletion": encoding["EnableSegmentDeletion"],
                "segment_keep_seconds": encoding["SegmentKeepSeconds"],
            }
        )
    )


if __name__ == "__main__":
    main()
