from __future__ import annotations

import json

from app.config import settings
from app.db import Database
from app.scanner import CatalogScanner


if __name__ == "__main__":
    database = Database(settings.database)
    database.initialize()
    updated = CatalogScanner(database, settings.readonly_root).enrich_audio_metadata()
    print(json.dumps({"updated": updated}))
