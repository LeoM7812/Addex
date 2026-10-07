"""Stremio ID formats.

Stremio IDs are plain strings. IMDb IDs are bare (`tt0944947`); every other catalog uses a
`scheme:value` prefix (`kitsu:1376`, `mal:5114`). Episodes append `:season:episode` for IMDb
and `:episode` for the anime schemes (`tt0944947:1:1`, `kitsu:1376:1`).
"""

from enum import StrEnum


class IdScheme(StrEnum):
    IMDB = "imdb"
    KITSU = "kitsu"
    MAL = "mal"
    ANILIST = "anilist"
    TMDB = "tmdb"


def stremio_id(scheme: IdScheme, value: str) -> str:
    """Base Stremio ID for a title (movie, or a series as a whole)."""
    if scheme is IdScheme.IMDB:
        return value if value.startswith("tt") else f"tt{value}"
    return f"{scheme.value}:{value}"


def probe_id(scheme: IdScheme, value: str, stremio_type: str) -> str:
    """Stremio ID to send to /stream/ when checking a title.

    Addons answer stream requests per episode, so a series is probed through its first
    episode. Anime schemes number episodes absolutely; IMDb uses season and episode.
    """
    base = stremio_id(scheme, value)
    if stremio_type != "series":
        return base
    if scheme is IdScheme.IMDB:
        return f"{base}:1:1"
    return f"{base}:1"
