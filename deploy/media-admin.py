#!/usr/bin/python3
from __future__ import annotations

import json
import os
import shutil
import socketserver
import uuid
from pathlib import Path


ROOT = Path("/mnt/media").resolve()
SOCKET = Path("/var/lib/media-center-bridge/admin/admin.sock")
PROTECTED_ROOTS = {"amintiri", "audio", "video"}


def resolve_relative(value: str) -> Path:
    relative = Path(value or "")
    if relative.is_absolute() or ".." in relative.parts or not relative.parts:
        raise ValueError("Invalid media path")
    candidate = (ROOT / relative).resolve(strict=True)
    if candidate == ROOT or ROOT not in candidate.parents:
        raise ValueError("Path escapes media root")
    return candidate


def valid_name(value: str) -> str:
    if not value or value in {".", ".."} or "/" in value or "\\" in value or "\x00" in value:
        raise ValueError("Invalid destination name")
    return value


def destination(parent_value: str, name: str) -> Path:
    parent = ROOT if not parent_value else resolve_relative(parent_value)
    if not parent.is_dir():
        raise ValueError("Destination is not a directory")
    candidate = parent / valid_name(name)
    if candidate.exists():
        raise FileExistsError("Destination already exists")
    return candidate


def perform(payload: dict) -> dict:
    action = payload.get("action")
    if action == "mkdir":
        created = destination(str(payload.get("path", "")), str(payload.get("name", "")))
        created.mkdir()
        return {"ok": True, "path": created.relative_to(ROOT).as_posix()}
    target = resolve_relative(str(payload.get("path", "")))
    if target.parent == ROOT and target.name in PROTECTED_ROOTS and action in {"rename", "move", "delete"}:
        raise ValueError("Library roots cannot be renamed, moved, or deleted")
    if action == "rename":
        new_name = valid_name(str(payload.get("new_name", "")))
        renamed = target.with_name(new_name)
        if renamed.exists():
            raise FileExistsError("Destination already exists")
        target.rename(renamed)
        return {"ok": True, "path": renamed.relative_to(ROOT).as_posix()}
    if action == "move":
        destination_dir = ROOT if not payload.get("destination") else resolve_relative(str(payload["destination"]))
        if not destination_dir.is_dir():
            raise ValueError("Destination is not a directory")
        moved = destination_dir / target.name
        if moved.exists():
            raise FileExistsError("Destination already exists")
        if target.is_dir() and (destination_dir == target or target in destination_dir.parents):
            raise ValueError("Cannot move a directory inside itself")
        target.rename(moved)
        return {"ok": True, "path": moved.relative_to(ROOT).as_posix()}
    if action == "delete":
        if payload.get("confirm_name") != target.name:
            raise ValueError("Confirmation does not match")
        if target.is_dir() and not target.is_symlink():
            shutil.rmtree(target)
        else:
            target.unlink()
        return {"ok": True}
    raise ValueError("Unsupported operation")


def receive_upload(payload: dict, source) -> dict:
    size = int(payload.get("size", -1))
    if size < 0:
        raise ValueError("Invalid upload size")
    final = destination(str(payload.get("path", "")), str(payload.get("name", "")))
    temporary = final.with_name(f".{final.name}.upload-{uuid.uuid4().hex}.part")
    remaining = size
    try:
        with temporary.open("xb") as output:
            while remaining:
                chunk = source.read(min(1024 * 1024, remaining))
                if not chunk:
                    raise ValueError("Upload ended before the declared size")
                output.write(chunk)
                remaining -= len(chunk)
            output.flush()
            os.fsync(output.fileno())
        temporary.rename(final)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return {"ok": True, "path": final.relative_to(ROOT).as_posix(), "size": size}


class Handler(socketserver.StreamRequestHandler):
    def handle(self):
        try:
            payload = json.loads(self.rfile.readline(1024 * 1024))
            result = receive_upload(payload, self.rfile) if payload.get("action") == "upload" else perform(payload)
        except Exception as exc:
            result = {"ok": False, "error": str(exc)}
        self.wfile.write(json.dumps(result).encode() + b"\n")


class Server(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True


def main():
    SOCKET.parent.mkdir(parents=True, exist_ok=True)
    SOCKET.unlink(missing_ok=True)
    with Server(str(SOCKET), Handler) as server:
        os.chmod(SOCKET, 0o666)
        server.serve_forever()


if __name__ == "__main__":
    main()
