from sqlalchemy import select

from addex_core.animelists import parse_kitsu_to_imdb
from addex_core.ids import IdScheme
from addex_core.linking import link_anime
from addex_core.models import Title, TitleId
from addex_core.titles import TitleSeed, upsert_titles


def test_parse_kitsu_to_imdb():
    entries = [
        {"kitsu_id": 7442, "imdb_id": ["tt2560140"]},
        {"kitsu_id": 8671, "imdb_id": ["tt2560140", "tt9999999"]},
        {"kitsu_id": 1, "imdb_id": None},
        {"mal_id": 5, "imdb_id": ["tt1"]},
        {"kitsu_id": 7442, "imdb_id": ["tt0000000"]},  # duplicate: first wins
    ]
    assert parse_kitsu_to_imdb(entries) == {"7442": "tt2560140", "8671": "tt2560140"}


def _kitsu(kid, name, type_="series"):
    return TitleSeed(type=type_, name=name, ids={IdScheme.KITSU: kid}, is_anime=True)


async def _by_id(session, scheme, value) -> Title:
    return await session.scalar(
        select(Title).join(TitleId).where(TitleId.scheme == scheme, TitleId.value == value)
    )


def test_link_anime(db):
    mapping = {"1": "tt100", "2": "tt100", "3": "tt200", "4": "tt404"}
    fetch_calls = []

    async def fetch_parents(wanted):
        fetch_calls.append(dict(wanted))
        return {
            i: TitleSeed(type="movie", name="Movie 200", ids={IdScheme.IMDB: i})
            if i == "tt200" else None
            for i in wanted
        }

    async def scenario(session):
        await upsert_titles(session, [
            _kitsu("1", "Show S1"), _kitsu("2", "Show S2"), _kitsu("3", "Movie", "movie"),
            _kitsu("4", "Lost"), _kitsu("5", "Unmapped"),
            TitleSeed(type="series", name="Show", ids={IdScheme.IMDB: "tt100"}),
        ])

        report = await link_anime(session, mapping, fetch_parents)
        assert fetch_calls == [{"tt200": ("movie", "series"), "tt404": ("series", "movie")}]
        assert (report.linked, report.unmapped, report.parents_created) == (3, 1, 1)
        assert report.parents_missing == ["tt404"]

        show = await _by_id(session, IdScheme.IMDB, "tt100")
        movie = await _by_id(session, IdScheme.IMDB, "tt200")
        assert show.is_anime and movie.is_anime
        for kid, parent in (("1", show), ("2", show), ("3", movie)):
            assert (await _by_id(session, IdScheme.KITSU, kid)).parent_id == parent.id
        for kid in ("4", "5"):
            assert (await _by_id(session, IdScheme.KITSU, kid)).parent_id is None

        # Second run: nothing new to fetch except the still-missing parent, nothing changes.
        again = await link_anime(session, mapping, fetch_parents)
        assert fetch_calls[-1] == {"tt404": ("series", "movie")}
        assert (again.changed, again.parents_created) == (0, 0)

        # A mapping that disappears unlinks.
        del mapping["2"]
        third = await link_anime(session, mapping, fetch_parents)
        assert third.changed == 1
        assert (await _by_id(session, IdScheme.KITSU, "2")).parent_id is None

    db(scenario)
