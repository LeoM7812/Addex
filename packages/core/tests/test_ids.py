import pytest

from addex_core.ids import IdScheme, probe_id, stremio_id


@pytest.mark.parametrize(
    "scheme, value, type_, expected",
    [
        (IdScheme.IMDB, "tt0111161", "movie", "tt0111161"),
        (IdScheme.IMDB, "0944947", "series", "tt0944947:1:1"),
        (IdScheme.KITSU, "1376", "series", "kitsu:1376:1"),
        (IdScheme.KITSU, "1376", "movie", "kitsu:1376"),
        (IdScheme.MAL, "5114", "series", "mal:5114:1"),
    ],
)
def test_probe_id(scheme, value, type_, expected):
    assert probe_id(scheme, value, type_) == expected


def test_stremio_id():
    assert stremio_id(IdScheme.ANILIST, "21") == "anilist:21"
