from app.episode_sync import (
    EpisodeEnricher,
    clean_summary,
    episode_label,
    episode_numbers,
    is_manual_override,
    thetvdb_episodes,
)


def test_explicit_season_and_episode_are_parsed():
    assert episode_numbers("Show/Season 2/Show.S02E07.Title.mkv") == (2, 7, "explicit")
    assert episode_label("Show/Season 2/Show.S02E07.Title.mkv") == "S02E07"


def test_episode_number_uses_season_folder():
    assert episode_numbers("Show/Sezonul 3/Show E04.Title.mkv") == (3, 4, "episode")


def test_root_episode_defaults_to_first_season():
    assert episode_numbers("Miniseries/Episodul 09.The Passion.mkv") == (1, 9, "episode")


def test_roman_part_is_supported():
    assert episode_numbers("Miniseries/The Dovekeepers_II.480p.mp4") == (1, 2, "roman")


def test_unique_filename_title_overrides_shifted_episode_number():
    emancipation = {"imdb_id": "tt0709075", "title": "Emancipation"}
    enemy_within = {"imdb_id": "tt0709185", "title": "The Enemy Within"}

    selected = EpisodeEnricher._match_episode(
        {"name": "SG-1 S01E03.The Enemy Within.1080p.x265.mkv"},
        {"title": "Stargate SG-1"},
        [enemy_within, emancipation],
        emancipation,
    )

    assert selected == enemy_within


def test_automatic_episode_matches_can_be_recalculated():
    assert not is_manual_override("manual_episode")
    assert is_manual_override("manual")
    assert is_manual_override("manual_confirmed")


def test_tvmaze_html_summary_is_cleaned():
    assert clean_summary("<p>A first line &amp; <b>important</b> detail.</p>") == "A first line & important detail."


def test_thetvdb_episode_list_is_parsed_by_exact_number():
    page = '''
    <li class="list-group-item">
      <span class="episode-label">S01E02</span>
      <a href="/series/example/episodes/42">Second episode</a>
      <div class="list-group-item-text"><p>A sufficiently long &amp; exact episode summary for local storage.</p></div>
    </li>
    '''
    assert thetvdb_episodes(page)[(1, 2)] == (
        "Second episode",
        "A sufficiently long & exact episode summary for local storage.",
        "/series/example/episodes/42",
    )
