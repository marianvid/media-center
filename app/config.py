from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    readonly_root: Path = Path(os.getenv("MEDIA_READONLY_ROOT", "/srv/media/readonly"))
    manage_root: Path = Path(os.getenv("MEDIA_MANAGE_ROOT", "/srv/media/manage"))
    state_dir: Path = Path(os.getenv("MEDIA_STATE_DIR", "/var/lib/media-center"))
    cache_dir: Path = Path(os.getenv("MEDIA_CACHE_DIR", "/var/cache/media-center"))
    health_file: Path = Path(os.getenv("MEDIA_HEALTH_FILE", "/srv/bridge/health/health.json"))
    admin_socket: Path = Path(os.getenv("MEDIA_ADMIN_SOCKET", "/srv/bridge/admin/admin.sock"))
    imdb_cache_dir: Path = Path(os.getenv("IMDB_CACHE_DIR", "/var/cache/media-center/imdb"))
    jellyfin_url: str = os.getenv("JELLYFIN_URL", "")
    jellyfin_public_url: str = os.getenv("JELLYFIN_PUBLIC_URL", "")
    jellyfin_token: str = os.getenv("JELLYFIN_TOKEN", "")
    jellyfin_admin_token: str = os.getenv("JELLYFIN_ADMIN_TOKEN", "")
    jellyfin_user_id: str = os.getenv("JELLYFIN_USER_ID", "")
    jellyfin_database: Path = Path(
        os.getenv("JELLYFIN_DATABASE", "/var/lib/jellyfin/data/jellyfin.db")
    )

    @property
    def database(self) -> Path:
        return self.state_dir / "catalog.sqlite3"


settings = Settings()
