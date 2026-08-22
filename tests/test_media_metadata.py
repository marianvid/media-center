from app.media_metadata import audio_genres, image_year, path_year


def test_image_year_prefers_year_folder():
    assert image_year("2024/Trip/IMG_0001.jpg", 0) == 2024


def test_path_year_prefers_tag_then_last_path_year():
    assert path_year("Artist/Album (1998)/track.mp3", 0, "2003-04-01") == 2003
    assert path_year("Artist/Album (1998)/track.mp3", 0) == 1998


def test_audio_genre_uses_reliable_library_category():
    assert audio_genres("Muzica clasica/Bach/track.mp3", ["Other"]) == "Classical"
    assert audio_genres("Audio_Religioase/recording.mp3", []) == "Religious"
    assert audio_genres("Muzica_Diverse/track.mp3", ["genre"]) == "Various"


def test_audio_genre_keeps_useful_tags_and_category():
    assert audio_genres("Muzica_Nunta/track.mp3", ["Rock"]) == "Rock,Wedding"
