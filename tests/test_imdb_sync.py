from app.imdb_sync import select_candidate


def candidate(imdb_id, title_type, votes, rating=7.0):
    return {"imdb_id": imdb_id, "type": title_type, "votes": votes, "rating": rating}


def test_series_type_and_votes_select_the_real_show():
    selected, confidence = select_candidate(
        [candidate("tt-wrong", "movie", 12), candidate("tt-lost", "tvSeries", 679_023)],
        "series",
        2004,
    )
    assert selected["imdb_id"] == "tt-lost"
    assert confidence == "high"


def test_popular_movie_wins_same_title_and_year():
    selected, confidence = select_candidate(
        [candidate("tt-obscure", "movie", 15), candidate("tt-real", "movie", 150_000)],
        "movie",
        1989,
    )
    assert selected["imdb_id"] == "tt-real"
    assert confidence == "high"


def test_missing_year_stays_ambiguous_without_decisive_popularity():
    selected, confidence = select_candidate(
        [candidate("tt-one", "movie", 200), candidate("tt-two", "movie", 100)],
        "movie",
        None,
    )
    assert selected["imdb_id"] == "tt-one"
    assert confidence == "ambiguous"
