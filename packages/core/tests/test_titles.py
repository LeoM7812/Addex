from sqlalchemy import select
from sqlalchemy.orm import selectinload

from addex_core.ids import IdScheme
from addex_core.models import Title
from addex_core.titles import TitleSeed, upsert_titles

AOT = TitleSeed(
    type="series",
    name="Attack on Titan",
    ids={IdScheme.KITSU: "7442", IdScheme.MAL: "16498"},
    year=2013,
    is_anime=True,
    popularity_rank=1,
    aliases=("Attack on Titan", "Shingeki no Kyojin"),
)


async def _titles(session) -> list[Title]:
    return list(
        await session.scalars(
            select(Title).options(selectinload(Title.external_ids)).order_by(Title.id)
        )
    )


def test_insert_then_update(db):
    async def scenario(session):
        report = await upsert_titles(session, [AOT])
        assert (report.added, report.updated) == (1, 0)

        moved = TitleSeed(**{**AOT.__dict__, "popularity_rank": 3,
                             "ids": {**AOT.ids, IdScheme.ANILIST: "16498"}})
        report = await upsert_titles(session, [moved])
        assert (report.added, report.updated) == (0, 1)

        [title] = await _titles(session)
        assert title.popularity_rank == 3
        assert title.aliases == ["Shingeki no Kyojin"]  # name itself is not an alias
        assert {(x.scheme, x.value) for x in title.external_ids} == {
            (IdScheme.KITSU, "7442"), (IdScheme.MAL, "16498"), (IdScheme.ANILIST, "16498"),
        }

    db(scenario)


def test_conflicting_secondary_id_is_skipped(db):
    async def scenario(session):
        other = TitleSeed(type="series", name="Dupe", ids={IdScheme.KITSU: "1", IdScheme.MAL: "16498"})
        report = await upsert_titles(session, [AOT, other])
        assert report.added == 2
        assert [(s, v) for s, v, _ in report.id_conflicts] == [(IdScheme.MAL, "16498")]

        aot, dupe = await _titles(session)
        assert {x.scheme for x in aot.external_ids} == {IdScheme.KITSU, IdScheme.MAL}
        assert {x.scheme for x in dupe.external_ids} == {IdScheme.KITSU}

    db(scenario)
