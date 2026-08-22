from __future__ import annotations

import asyncio
import json
import socket
from pathlib import Path


class AdminError(RuntimeError):
    pass


class AdminClient:
    def __init__(self, socket_path: Path):
        self.socket_path = socket_path

    def request(self, payload: dict) -> dict:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(60)
            client.connect(str(self.socket_path))
            client.sendall(json.dumps(payload).encode() + b"\n")
            response = b""
            while not response.endswith(b"\n"):
                chunk = client.recv(65536)
                if not chunk:
                    break
                response += chunk
        data = json.loads(response)
        if not data.get("ok"):
            raise AdminError(data.get("error", "Administrative operation failed"))
        return data

    async def upload(self, payload: dict, chunks) -> dict:
        try:
            reader, writer = await asyncio.open_unix_connection(str(self.socket_path))
            writer.write(json.dumps(payload).encode() + b"\n")
            await writer.drain()
            async for chunk in chunks:
                writer.write(chunk)
                await writer.drain()
            response = await asyncio.wait_for(reader.readline(), timeout=120)
            writer.close()
            await writer.wait_closed()
        except (OSError, asyncio.TimeoutError) as exc:
            raise AdminError(f"Administrative upload failed: {exc}") from exc
        data = json.loads(response)
        if not data.get("ok"):
            raise AdminError(data.get("error", "Administrative upload failed"))
        return data
