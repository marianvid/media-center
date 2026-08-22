from __future__ import annotations

import argparse
from pathlib import Path

from app.details_migration import migrate


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("database", type=Path)
    parser.add_argument("details_dir", type=Path)
    args = parser.parse_args()
    parsed, updated = migrate(args.database, args.details_dir)
    print(f"parsed={parsed} updated_imdb_ids={updated}")
