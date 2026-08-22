#!/usr/bin/python3
from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path


OUTPUT = Path("/var/lib/media-center-bridge/health/health.json")
MEDIA_LABEL = Path("/dev/disk/by-label/media")


def attribute(data: dict, number: int) -> int:
    for item in data.get("ata_smart_attributes", {}).get("table", []):
        if item.get("id") == number:
            return int(item.get("raw", {}).get("value", 0))
    return 0


def main():
    # The Linux sdX name changes when the dock is moved between USB ports.
    # Resolve the stable filesystem label to the parent disk every time.
    partition = MEDIA_LABEL.resolve(strict=True)
    parent_name = (Path("/sys/class/block") / partition.name).resolve().parent.name
    device = Path("/dev") / parent_name
    raw = subprocess.run(["smartctl", "-aj", str(device)], capture_output=True, text=True, check=False)
    data = json.loads(raw.stdout)
    passed = data.get("smart_status", {}).get("passed")
    result = {
        "available": True,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "model": data.get("model_name"),
        "serial": data.get("serial_number"),
        "capacity_bytes": data.get("user_capacity", {}).get("bytes"),
        "device": str(device),
        "smart_status": "PASSED" if passed is True else ("FAILED" if passed is False else "UNKNOWN"),
        "temperature_c": data.get("temperature", {}).get("current"),
        "power_on_hours": data.get("power_on_time", {}).get("hours"),
        "reallocated": attribute(data, 5),
        "pending": attribute(data, 197),
        "offline_uncorrectable": attribute(data, 198),
        "crc_errors": attribute(data, 199),
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    temp = OUTPUT.with_suffix(".tmp")
    temp.write_text(json.dumps(result, indent=2) + "\n")
    os.replace(temp, OUTPUT)


if __name__ == "__main__":
    main()
