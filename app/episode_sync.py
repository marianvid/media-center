from __future__ import annotations

import csv
import gzip
import html
import json
import re
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from pathlib import Path

from .db import Database
from .imdb_sync import BASE
from .scanner import title_from_name


SEASON_EPISODE = re.compile(
    r"(?i)(?:^|[ ._\-])S(?:eason)?\s*0*(\d{1,2})[ ._\-]*E(?:pisode)?\s*0*(\d{1,3})(?:\D|$)"
)
X_PATTERN = re.compile(r"(?i)(?:^|\D)0*(\d{1,2})x0*(\d{1,3})(?:\D|$)")
SEASON_PATH = re.compile(r"(?i)(?:sezon(?:ul)?|season)[ ._\-]*0*(\d{1,2})(?:\D|$)")
EPISODE_ONLY = re.compile(
    r"(?i)(?:^|[ ._\-])(?:E(?:p(?:isode|isod(?:ul)?)?)?|Episod(?:ul)?|Episode)"
    r"[ ._\-]*0*(\d{1,3})(?:\D|$)"
)
MISSPELLED_EPISODE = re.compile(r"(?i)(?:^|[ ._\-])Episoul[ ._\-]*0*(\d{1,3})(?:\D|$)")
PART_ONLY = re.compile(r"(?i)(?:partea|part)[ ._\-]*0*(\d{1,3})(?:\D|$)")
ROMAN_PART = re.compile(r"(?i)[ ._\-](I|II|III|IV|V|VI|VII|VIII|IX|X)(?:[ ._\-]|$)")
TAG = re.compile(r"<[^>]+>")
SPACE = re.compile(r"\s+")
NORMALIZE = re.compile(r"[^a-z0-9]+")
ROMAN = {"I": 1, "II": 2, "III": 3, "IV": 4, "V": 5, "VI": 6, "VII": 7, "VIII": 8, "IX": 9, "X": 10}
USER_AGENT = "HomeMediaCenter/1.0 (private local catalog enrichment)"


def normalized(value: str) -> str:
    return NORMALIZE.sub("", value.casefold())


def episode_numbers(path: str) -> tuple[int, int, str] | None:
    name = path.rsplit("/", 1)[-1]
    explicit = SEASON_EPISODE.search(name) or X_PATTERN.search(name)
    if explicit:
        return int(explicit.group(1)), int(explicit.group(2)), "explicit"

    season = None
    for part in path.split("/")[:-1]:
        match = SEASON_PATH.search(part)
        if match:
            season = int(match.group(1))

    episode = EPISODE_ONLY.search(name) or MISSPELLED_EPISODE.search(name)
    if episode:
        return season or 1, int(episode.group(1)), "episode"

    part = PART_ONLY.search(name)
    if part:
        return season or 1, int(part.group(1)), "part"

    roman = ROMAN_PART.search(Path(name).stem)
    if roman:
        return season or 1, ROMAN[roman.group(1).upper()], "roman"
    return None


def episode_label(path: str) -> str:
    numbers = episode_numbers(path)
    return f"S{numbers[0]:02d}E{numbers[1]:02d}" if numbers else ""


def clean_summary(value: str) -> str:
    return SPACE.sub(" ", html.unescape(TAG.sub(" ", value or ""))).strip()


def thetvdb_episodes(page: str) -> dict[tuple[int, int], tuple[str, str, str]]:
    episodes: dict[tuple[int, int], tuple[str, str, str]] = {}
    chunks = page.split('<li class="list-group-item">')[1:]
    for chunk in chunks:
        label = re.search(r"episode-label[^>]*>\s*S(\d+)E(\d+)", chunk, re.I)
        link = re.search(r'href="([^"]+/episodes/\d+)"', chunk, re.I)
        title = re.search(r'/episodes/\d+">\s*([\s\S]*?)\s*</a>', chunk, re.I)
        summary = re.search(r'list-group-item-text[\s\S]*?<p>([\s\S]*?)</p>', chunk, re.I)
        if not label or not link or not title or not summary:
            continue
        text = clean_summary(summary.group(1))
        if len(text) >= 40:
            episodes[(int(label.group(1)), int(label.group(2)))] = (
                clean_summary(title.group(1)), text, link.group(1)
            )
    return episodes


def choose_plot(edges: list[dict]) -> str:
    choices = [
        ((edge.get("node") or {}).get("plotText") or {}).get("plainText", "").strip()
        for edge in edges
    ]
    return next((plot for plot in choices if len(plot) >= 40), "")


class EpisodeEnricher:
    def __init__(self, db: Database, cache_dir: Path):
        self.db = db
        self.cache = cache_dir

    def _job(self, state: str, message: str, current: int = 0, total: int = 0) -> None:
        with self.db.connect() as db:
            db.execute(
                "INSERT INTO jobs(name,state,message,current,total,updated_at) VALUES('episodes',?,?,?,?,CURRENT_TIMESTAMP) "
                "ON CONFLICT(name) DO UPDATE SET state=excluded.state,message=excluded.message,current=excluded.current,"
                "total=excluded.total,updated_at=CURRENT_TIMESTAMP",
                (state, message, current, total),
            )

    def _download(self, name: str) -> Path:
        self.cache.mkdir(parents=True, exist_ok=True)
        target = self.cache / name
        if not target.exists():
            self._job("running", f"Downloading {name}")
            request = urllib.request.Request(BASE + name, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(request, timeout=120) as response, target.open("wb") as output:
                while chunk := response.read(1024 * 1024):
                    output.write(chunk)
        return target

    @staticmethod
    def _rows(path: Path):
        with gzip.open(path, "rt", encoding="utf-8", newline="") as handle:
            yield from csv.DictReader(handle, delimiter="\t")

    @staticmethod
    def _request_json(url: str, *, body: bytes | None = None, attempts: int = 3) -> dict | list | None:
        headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
        if body is not None:
            headers.update({
                "Content-Type": "application/json",
                "Origin": "https://www.imdb.com",
                "Referer": "https://www.imdb.com/",
                "X-Imdb-User-Language": "en-US",
            })
        for attempt in range(attempts):
            try:
                request = urllib.request.Request(url, data=body, headers=headers, method="POST" if body else "GET")
                with urllib.request.urlopen(request, timeout=60) as response:
                    return json.load(response)
            except (OSError, urllib.error.HTTPError, json.JSONDecodeError):
                if attempt + 1 < attempts:
                    time.sleep(1 + attempt * 2)
        return None

    def _local_items(self) -> tuple[dict[str, dict], list[dict]]:
        with self.db.connect() as db:
            folders = [dict(row) for row in db.execute(
                "SELECT rel_path,imdb_id,title,genres FROM items "
                "WHERE library='video' AND kind='directory' AND imdb_id IS NOT NULL"
            )]
            files = [dict(row) for row in db.execute(
                "SELECT id,rel_path,name,title,imdb_id,imdb_match_status,synopsis FROM items "
                "WHERE library='video' AND media_type='video'"
            )]
        return {folder["rel_path"]: folder for folder in folders}, files

    @staticmethod
    def _parent_for(path: str, folders: dict[str, dict]) -> dict | None:
        parts = path.split("/")[:-1]
        for count in range(len(parts), 0, -1):
            folder = folders.get("/".join(parts[:count]))
            if folder:
                return folder
        return None

    @staticmethod
    def _title_match(item: dict, parent: dict, episodes: list[dict]) -> dict | None:
        local_title, _year = title_from_name(item["name"])
        local = normalized(local_title)
        show = normalized(parent["title"] or "")
        if show and local.startswith(show):
            local = local[len(show):]
        if len(local) < 5:
            return None
        exact = [episode for episode in episodes if normalized(episode["title"]) == local]
        if len(exact) == 1:
            return exact[0]
        contained = [
            episode for episode in episodes
            if len(normalized(episode["title"])) >= 7 and normalized(episode["title"]) in local
        ]
        return contained[0] if len(contained) == 1 else None

    def _imdb_plots(self, imdb_ids: list[str]) -> dict[str, str]:
        plots: dict[str, str] = {}
        total = len(imdb_ids)
        for offset in range(0, total, 120):
            batch = imdb_ids[offset:offset + 120]
            fields = [
                f'e{index}: title(id: "{imdb_id}") {{ id plots(first: 3) {{ edges {{ node {{ plotText {{ plainText }} }} }} }} }}'
                for index, imdb_id in enumerate(batch)
            ]
            body = json.dumps({"query": "query { " + " ".join(fields) + " }"}).encode()
            payload = self._request_json("https://api.graphql.imdb.com/", body=body)
            data = payload.get("data", {}) if isinstance(payload, dict) else {}
            for value in data.values():
                if value and value.get("id"):
                    plot = choose_plot((value.get("plots") or {}).get("edges") or [])
                    if plot:
                        plots[value["id"]] = plot
            self._job("running", f"IMDb episode synopses: {min(offset + len(batch), total):,}/{total:,}", min(offset + len(batch), total), total)
        return plots

    def _tvmaze_plots(self, unresolved: list[dict]) -> dict[str, tuple[str, str]]:
        by_parent: dict[str, list[dict]] = defaultdict(list)
        for item in unresolved:
            by_parent[item["parent_imdb"]].append(item)
        plots: dict[str, tuple[str, str]] = {}
        for index, (parent_imdb, local_items) in enumerate(by_parent.items(), 1):
            lookup = self._request_json(
                "https://api.tvmaze.com/lookup/shows?" + urllib.parse.urlencode({"imdb": parent_imdb}),
                attempts=2,
            )
            show_id = lookup.get("id") if isinstance(lookup, dict) else None
            if not show_id:
                continue
            episodes = self._request_json(f"https://api.tvmaze.com/shows/{show_id}/episodes?specials=1", attempts=2)
            if not isinstance(episodes, list):
                continue
            by_number = {
                (episode.get("season"), episode.get("number")): episode
                for episode in episodes if episode.get("season") is not None and episode.get("number") is not None
            }
            for item in local_items:
                episode = by_number.get((item["season"], item["episode"]))
                summary = clean_summary((episode or {}).get("summary", ""))
                if len(summary) >= 40:
                    plots[item["imdb_id"]] = (summary, episode.get("url") or f"https://www.tvmaze.com/shows/{show_id}")
            if index % 25 == 0:
                self._job("running", f"TVMaze fallback: {index:,}/{len(by_parent):,} shows", index, len(by_parent))
                time.sleep(0.5)
        return plots

    def _thetvdb_plots(self, unresolved: list[dict]) -> dict[str, tuple[str, str]]:
        by_parent: dict[str, list[dict]] = defaultdict(list)
        for item in unresolved:
            by_parent[item["parent_imdb"]].append(item)
        plots: dict[str, tuple[str, str]] = {}
        for index, (parent_imdb, local_items) in enumerate(by_parent.items(), 1):
            query = local_items[0].get("parent_title") or parent_imdb
            body = json.dumps({
                "requests": [{"indexName": "TVDB", "params": {"query": query, "hitsPerPage": 20}}]
            }).encode()
            payload = self._request_json("https://api4.thetvdb.com/web/search/queries", body=body, attempts=2)
            results = payload.get("results", []) if isinstance(payload, dict) else []
            hits = results[0].get("hits", []) if results else []
            match = next((hit for hit in hits if hit.get("type") == "series" and any(
                remote.get("sourceName") == "IMDB" and remote.get("id") == parent_imdb
                for remote in hit.get("remote_ids", [])
            )), None)
            slug = (match or {}).get("slug")
            if not slug:
                continue
            url = f"https://thetvdb.com/series/{slug}/allseasons/official"
            try:
                request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
                with urllib.request.urlopen(request, timeout=60) as response:
                    page = response.read().decode("utf-8", "replace")
            except (OSError, urllib.error.HTTPError):
                continue
            episodes = thetvdb_episodes(page)
            for item in local_items:
                result = episodes.get((item["season"], item["episode"]))
                if not result:
                    title_matches = [
                        episode for episode in episodes.values()
                        if normalized(episode[0]) == normalized(item.get("title") or "")
                    ]
                    result = title_matches[0] if len(title_matches) == 1 else None
                if result:
                    _title, synopsis, path = result
                    plots[item["imdb_id"]] = (synopsis, "https://thetvdb.com" + path)
            if index % 10 == 0:
                self._job("running", f"TheTVDB fallback: {index:,}/{len(by_parent):,} shows", index, len(by_parent))
        return plots

    def run(self) -> dict:
        self._job("running", "Preparing local episodes")
        folders, files = self._local_items()
        local: list[dict] = []
        parent_ids: set[str] = set()
        for item in files:
            if item["imdb_match_status"].startswith("manual"):
                continue
            parent = self._parent_for(item["rel_path"], folders)
            if not parent:
                continue
            numbers = episode_numbers(item["rel_path"])
            item.update({
                "parent": parent,
                "parent_imdb": parent["imdb_id"],
                "season": numbers[0] if numbers else None,
                "episode": numbers[1] if numbers else None,
                "method": numbers[2] if numbers else "title",
            })
            local.append(item)
            parent_ids.add(parent["imdb_id"])

        episode_file = self._download("title.episode.tsv.gz")
        by_key: dict[tuple[str, int, int], str] = {}
        episode_parent: dict[str, str] = {}
        for row in self._rows(episode_file):
            if row["parentTconst"] not in parent_ids or not row["seasonNumber"].isdigit() or not row["episodeNumber"].isdigit():
                continue
            key = (row["parentTconst"], int(row["seasonNumber"]), int(row["episodeNumber"]))
            by_key[key] = row["tconst"]
            episode_parent[row["tconst"]] = row["parentTconst"]

        all_episode_ids = set(episode_parent)
        details: dict[str, dict] = {}
        basics = self._download("title.basics.tsv.gz")
        for row in self._rows(basics):
            if row["tconst"] not in all_episode_ids:
                continue
            details[row["tconst"]] = {
                "imdb_id": row["tconst"],
                "title": row["primaryTitle"],
                "year": int(row["startYear"]) if row["startYear"].isdigit() else None,
                "genres": row["genres"].replace("\\N", ""),
                "parent_imdb": episode_parent[row["tconst"]],
            }

        episodes_by_parent: dict[str, list[dict]] = defaultdict(list)
        for detail in details.values():
            episodes_by_parent[detail["parent_imdb"]].append(detail)

        matches: dict[int, dict] = {}
        matched_local: dict[int, dict] = {}
        for item in local:
            imdb_id = None
            if item["season"] is not None and item["episode"] is not None:
                imdb_id = by_key.get((item["parent_imdb"], item["season"], item["episode"]))
            detail = details.get(imdb_id) if imdb_id else self._title_match(item, item["parent"], episodes_by_parent[item["parent_imdb"]])
            if detail:
                matches[item["id"]] = detail
                item["imdb_id"] = detail["imdb_id"]
                matched_local[item["id"]] = item

        selected_ids = {detail["imdb_id"] for detail in matches.values()}
        ratings = self._download("title.ratings.tsv.gz")
        for row in self._rows(ratings):
            if row["tconst"] in selected_ids:
                details[row["tconst"]]["rating"] = float(row["averageRating"])

        title_people: dict[str, list[str]] = {imdb_id: [] for imdb_id in selected_ids}
        names_needed: set[str] = set()
        principals = self._download("title.principals.tsv.gz")
        for row in self._rows(principals):
            people = title_people.get(row["tconst"])
            if people is None or row["category"] not in {"actor", "actress", "self"} or len(people) >= 8:
                continue
            if row["nconst"] not in people:
                people.append(row["nconst"])
                names_needed.add(row["nconst"])

        names: dict[str, str] = {}
        names_file = self._download("name.basics.tsv.gz")
        for row in self._rows(names_file):
            if row["nconst"] in names_needed:
                names[row["nconst"]] = row["primaryName"]

        self._job("running", f"Saving {len(matches):,} matched episodes", 0, len(matches))
        with self.db.connect() as db:
            for item_id, detail in matches.items():
                item = matched_local[item_id]
                genres = detail["genres"] or item["parent"].get("genres", "")
                actors = ", ".join(names.get(person, person) for person in title_people[detail["imdb_id"]])
                db.execute(
                    "UPDATE items SET title=?,year=?,imdb_id=?,imdb_rating=?,genres=?,actors=?,"
                    "imdb_match_status='manual_episode',"
                    "synopsis=CASE WHEN imdb_id=? THEN synopsis ELSE '' END,"
                    "synopsis_language=CASE WHEN imdb_id=? THEN synopsis_language ELSE '' END,"
                    "synopsis_source=CASE WHEN imdb_id=? THEN synopsis_source ELSE '' END,"
                    "synopsis_retrieved_at=CASE WHEN imdb_id=? THEN synopsis_retrieved_at ELSE '' END WHERE id=?",
                    (detail["title"], detail["year"], detail["imdb_id"], detail.get("rating"), genres, actors,
                     detail["imdb_id"], detail["imdb_id"], detail["imdb_id"], detail["imdb_id"], item_id),
                )

        with self.db.connect() as db:
            missing_ids = [row["imdb_id"] for row in db.execute(
                "SELECT DISTINCT imdb_id FROM items WHERE imdb_match_status='manual_episode' AND synopsis='' AND imdb_id IS NOT NULL"
            )]
        imdb_plots = self._imdb_plots(missing_ids)
        retrieved_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        with self.db.connect() as db:
            for imdb_id, synopsis in imdb_plots.items():
                db.execute(
                    "UPDATE items SET synopsis=?,synopsis_language='en',synopsis_source=?,synopsis_retrieved_at=? "
                    "WHERE imdb_id=? AND imdb_match_status='manual_episode' AND synopsis=''",
                    (synopsis, f"https://www.imdb.com/title/{imdb_id}/", retrieved_at, imdb_id),
                )

        with self.db.connect() as db:
            unresolved_ids = {row["imdb_id"] for row in db.execute(
                "SELECT DISTINCT imdb_id FROM items WHERE imdb_match_status='manual_episode' AND synopsis='' AND imdb_id IS NOT NULL"
            )}
        unresolved: list[dict] = []
        for item in files:
            if item.get("imdb_id") not in unresolved_ids:
                continue
            parent = self._parent_for(item["rel_path"], folders)
            numbers = episode_numbers(item["rel_path"])
            if not parent or not numbers:
                continue
            item.update({
                "parent_imdb": parent["imdb_id"],
                "parent_title": parent.get("title") or parent.get("name") or "",
                "season": numbers[0],
                "episode": numbers[1],
            })
            unresolved.append(item)
        tvmaze_plots = self._tvmaze_plots(unresolved)
        with self.db.connect() as db:
            for imdb_id, (synopsis, source) in tvmaze_plots.items():
                db.execute(
                    "UPDATE items SET synopsis=?,synopsis_language='en',synopsis_source=?,synopsis_retrieved_at=? "
                    "WHERE imdb_id=? AND imdb_match_status='manual_episode' AND synopsis=''",
                    (synopsis, source, retrieved_at, imdb_id),
                )

        unresolved = [item for item in unresolved if item["imdb_id"] not in tvmaze_plots]
        thetvdb_plots = self._thetvdb_plots(unresolved)
        with self.db.connect() as db:
            for imdb_id, (synopsis, source) in thetvdb_plots.items():
                db.execute(
                    "UPDATE items SET synopsis=?,synopsis_language='en',synopsis_source=?,synopsis_retrieved_at=? "
                    "WHERE imdb_id=? AND imdb_match_status='manual_episode' AND synopsis=''",
                    (synopsis, source, retrieved_at, imdb_id),
                )
        with self.db.connect() as db:
            summary = dict(db.execute(
                "SELECT COUNT(*) AS matched_rows,COUNT(DISTINCT imdb_id) AS matched_titles,"
                "SUM(CASE WHEN synopsis<>'' THEN 1 ELSE 0 END) AS rows_with_synopsis,"
                "COUNT(DISTINCT CASE WHEN synopsis<>'' THEN imdb_id END) AS titles_with_synopsis "
                "FROM items WHERE imdb_match_status='manual_episode'"
            ).fetchone())

        summary.update({
            "eligible_local_rows": len(local),
            "matched_this_run": len(matches),
            "imdb_plots_added": len(imdb_plots),
            "tvmaze_plots_added": len(tvmaze_plots),
            "thetvdb_plots_added": len(thetvdb_plots),
        })
        self._job(
            "complete",
            f"Episodes: {summary['matched_titles']:,} matched · {summary['titles_with_synopsis']:,} with synopsis",
            summary["titles_with_synopsis"],
            summary["matched_titles"],
        )
        return summary
