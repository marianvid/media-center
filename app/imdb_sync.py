from __future__ import annotations

import collections
import csv
import gzip
import re
import threading
import urllib.request
from pathlib import Path

from .db import Database


BASE = "https://datasets.imdbws.com/"
FILES = ("title.basics.tsv.gz", "title.ratings.tsv.gz", "title.principals.tsv.gz", "name.basics.tsv.gz")
TITLE_TYPES = {"movie", "tvMovie", "tvSeries", "tvMiniSeries"}
NORMALIZE = re.compile(r"[^a-z0-9]+")


def normalized(value: str) -> str:
    return NORMALIZE.sub("", value.casefold())


def type_score(candidate: dict, family: str) -> int:
    if family == "series":
        return int(candidate["type"] in {"tvSeries", "tvMiniSeries"})
    return int(candidate["type"] in {"movie", "tvMovie"})


def select_candidate(candidates: list[dict], family: str, year: int | None) -> tuple[dict | None, str]:
    ranked = sorted(
        candidates,
        key=lambda candidate: (type_score(candidate, family), candidate.get("votes", 0), candidate.get("rating", 0)),
        reverse=True,
    )
    if not ranked:
        return None, "none"
    top = ranked[0]
    competitors = [candidate for candidate in ranked[1:] if type_score(candidate, family) == type_score(top, family)]
    second_votes = competitors[0].get("votes", 0) if competitors else 0
    votes = top.get("votes", 0)
    if year is not None:
        high_confidence = not competitors or votes >= max(50, second_votes * 3)
    else:
        high_confidence = votes >= 500 and (not competitors or votes >= max(100, second_votes * 5))
    return top, "high" if high_confidence else "ambiguous"


class ImdbSync:
    def __init__(self, db: Database, cache_dir: Path):
        self.db = db
        self.cache = cache_dir
        self._lock = threading.Lock()

    def start(self) -> bool:
        if not self._lock.acquire(blocking=False):
            return False
        threading.Thread(target=self._run_guarded, daemon=True, name="imdb-sync").start()
        return True

    def _run_guarded(self):
        try:
            self.sync()
        except Exception as exc:
            self._job("error", str(exc))
        finally:
            self._lock.release()

    def _job(self, state: str, message: str, current: int = 0):
        with self.db.connect() as db:
            db.execute(
                "INSERT INTO jobs(name,state,message,current,updated_at) VALUES('imdb',?,?,?,CURRENT_TIMESTAMP) "
                "ON CONFLICT(name) DO UPDATE SET state=excluded.state,message=excluded.message,current=excluded.current,updated_at=CURRENT_TIMESTAMP",
                (state, message, current),
            )

    def _download(self, name: str) -> Path:
        self.cache.mkdir(parents=True, exist_ok=True)
        target = self.cache / name
        if not target.exists():
            self._job("running", f"Downloading {name}")
            urllib.request.urlretrieve(BASE + name, target)
        return target

    @staticmethod
    def _rows(path: Path):
        with gzip.open(path, "rt", encoding="utf-8", newline="") as handle:
            yield from csv.DictReader(handle, delimiter="\t")

    @staticmethod
    def _family(item, video_paths: list[str]) -> str:
        if item["kind"] != "directory":
            return "movie"
        prefix = item["rel_path"].rstrip("/") + "/"
        descendants = sum(path.startswith(prefix) for path in video_paths)
        return "series" if descendants >= 2 else "movie"

    def sync(self):
        self._job("running", "Preparing IMDb candidates")
        with self.db.connect() as db:
            local = db.execute(
                "SELECT id,rel_path,title,year,kind,imdb_id,imdb_match_status FROM items "
                "WHERE library='video' AND media_type IN ('directory','video') AND title<>''"
            ).fetchall()
            video_paths = [row["rel_path"] for row in db.execute(
                "SELECT rel_path FROM items WHERE library='video' AND media_type='video'"
            )]

        item_info = {
            row["id"]: {
                "id": row["id"], "title": row["title"], "year": row["year"], "kind": row["kind"],
                "rel_path": row["rel_path"], "current_id": row["imdb_id"], "family": self._family(row, video_paths),
            }
            for row in local
            if not row["imdb_match_status"].startswith("manual")
        }
        wanted: dict[str, list[dict]] = collections.defaultdict(list)
        for item in item_info.values():
            wanted[normalized(item["title"])].append(item)

        candidates: dict[int, dict[str, dict]] = collections.defaultdict(dict)
        basics = self._download("title.basics.tsv.gz")
        for index, row in enumerate(self._rows(basics), 1):
            if row["titleType"] not in TITLE_TYPES:
                continue
            year = int(row["startYear"]) if row["startYear"].isdigit() else None
            names = {normalized(row["primaryTitle"]), normalized(row["originalTitle"])}
            for name in names:
                for item in wanted.get(name, []):
                    if item["year"] is not None and year != item["year"]:
                        continue
                    candidates[item["id"]][row["tconst"]] = {
                        "imdb_id": row["tconst"], "title": row["primaryTitle"], "year": year,
                        "type": row["titleType"], "genres": row["genres"].replace("\\N", ""),
                    }
            if index % 1_000_000 == 0:
                self._job("running", f"IMDb titles checked: {index:,}", index)

        candidate_refs: dict[str, list[dict]] = collections.defaultdict(list)
        for values in candidates.values():
            for imdb_id, candidate in values.items():
                candidate_refs[imdb_id].append(candidate)
        ratings = self._download("title.ratings.tsv.gz")
        for row in self._rows(ratings):
            for candidate in candidate_refs.get(row["tconst"], []):
                candidate["rating"] = float(row["averageRating"])
                candidate["votes"] = int(row["numVotes"])

        decisions: dict[int, tuple[dict | None, str]] = {}
        selected_ids: set[str] = set()
        changed = 0
        ambiguous = 0
        for item_id, item in item_info.items():
            top, confidence = select_candidate(list(candidates.get(item_id, {}).values()), item["family"], item["year"])
            if top and confidence == "high":
                decisions[item_id] = (top, "matched")
                selected_ids.add(top["imdb_id"])
                changed += int(bool(item["current_id"]) and top["imdb_id"] != item["current_id"])
            elif top:
                decisions[item_id] = (None, "ambiguous")
                ambiguous += 1
            else:
                decisions[item_id] = (None, "unmatched")

        principals = self._download("title.principals.tsv.gz")
        names_needed: set[str] = set()
        title_names: dict[str, list[str]] = {key: [] for key in selected_ids}
        for row in self._rows(principals):
            title = row["tconst"]
            person = row["nconst"]
            if (
                title in title_names
                and row["category"] in {"actor", "actress", "self"}
                and person not in title_names[title]
                and len(title_names[title]) < 8
            ):
                title_names[title].append(person)
                names_needed.add(person)

        names_file = self._download("name.basics.tsv.gz")
        names: dict[str, str] = {}
        for row in self._rows(names_file):
            if row["nconst"] in names_needed:
                names[row["nconst"]] = row["primaryName"]

        with self.db.connect() as db:
            for item_id, (candidate, status) in decisions.items():
                item = item_info[item_id]
                if status == "matched" and candidate:
                    actors = ", ".join(names.get(person, person) for person in title_names[candidate["imdb_id"]])
                    changed_id = bool(item["current_id"]) and candidate["imdb_id"] != item["current_id"]
                    db.execute(
                        "UPDATE items SET imdb_id=?,imdb_rating=?,genres=?,actors=?,year=COALESCE(year,?),"
                        "imdb_match_status='matched',"
                        "synopsis=CASE WHEN ? THEN '' ELSE synopsis END,"
                        "synopsis_language=CASE WHEN ? THEN '' ELSE synopsis_language END,"
                        "synopsis_source=CASE WHEN ? THEN '' ELSE synopsis_source END,"
                        "synopsis_retrieved_at=CASE WHEN ? THEN '' ELSE synopsis_retrieved_at END WHERE id=?",
                        (candidate["imdb_id"], candidate.get("rating"), candidate["genres"], actors, candidate["year"],
                         changed_id, changed_id, changed_id, changed_id, item_id),
                    )
                elif status == "ambiguous":
                    db.execute(
                        "UPDATE items SET imdb_rating=NULL,genres='',actors='',imdb_match_status='ambiguous',"
                        "synopsis='',synopsis_language='',synopsis_source='',synopsis_retrieved_at='' WHERE id=?",
                        (item_id,),
                    )
                else:
                    db.execute("UPDATE items SET imdb_match_status='unmatched' WHERE id=?", (item_id,))
        self._job(
            "complete",
            f"IMDb matched {len(selected_ids):,} titles · corrected {changed:,} items · {ambiguous:,} ambiguous",
            len(selected_ids),
        )
