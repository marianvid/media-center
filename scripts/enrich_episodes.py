from __future__ import annotations

import json

from app.config import settings
from app.db import Database
from app.episode_sync import EpisodeEnricher


if __name__ == "__main__":
    database = Database(settings.database)
    database.initialize()
    print(json.dumps(EpisodeEnricher(database, settings.imdb_cache_dir).run(), indent=2))
