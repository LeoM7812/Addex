from datetime import UTC, datetime, timedelta

from addex_core.ids import IdScheme
from addex_core.manifest import StreamScope
from addex_core.models import Addon, AddonStatus, Availability, CheckStatus, Title, TitleId
from addex_crawler.scheduler import pick_probe_id, due_jobs

TT_KITSU = [StreamScope(frozenset({"movie", "series"}), ("tt", "kitsu"))]
TT_ONLY = [StreamScope(frozenset({"movie", "series"}), ("tt",))]
MAL_ONLY = [StreamScope(frozenset({"series"}), ("mal:",))]
ANIME_IDS = {IdScheme.KITSU: "7442", IdScheme.MAL: "16498", IdScheme.IMDB: "tt2560140"}


def test_pick_probe_id_prefers_kitsu_for_anime():
    assert pick_probe_id(TT_KITSU, "series", ANIME_IDS, is_anime=True) == "kitsu:7442:1"
    assert pick_probe_id(TT_KITSU, "series", ANIME_IDS, is_anime=False) == "tt2560140:1:1"


def test_pick_probe_id_falls_back_to_what_the_addon_accepts():
    assert pick_probe_id(TT_ONLY, "series", ANIME_IDS, is_anime=True) == "tt2560140:1:1"
    assert pick_probe_id(MAL_ONLY, "series", ANIME_IDS, is_anime=True) == "mal:16498:1"
    assert pick_probe_id(MAL_ONLY, "movie", ANIME_IDS, is_anime=True) is None
    assert pick_probe_id(TT_ONLY, "series", {IdScheme.KITSU: "1"}, is_anime=True) is None


def _addon(url, scopes, status=AddonStatus.ACTIVE):
    return Addon(
        manifest_url=f"{url}/manifest.json", base_url=url, manifest_id="x", name=url,
        version="1", stream_scopes=[s.to_json() for s in scopes], p2p=False, adult=False,
        manifest={}, status=status,
    )


def _title(name, rank, kitsu):
    return Title(type="series", name=name, is_anime=True, popularity_rank=rank,
                 external_ids=[TitleId(scheme=IdScheme.KITSU, value=kitsu)])


def test_due_jobs(db):
    now = datetime.now(UTC)

    async def scenario(session):
        kitsu_addon = _addon("https://a.example", TT_KITSU)
        imdb_addon = _addon("https://b.example", TT_ONLY)
        off = _addon("https://c.example", TT_KITSU, AddonStatus.BROKEN)
        popular, fresh, stale = _title("P", 1, "1"), _title("F", 2, "2"), _title("S", 3, "3")
        session.add_all([kitsu_addon, imdb_addon, off, popular, fresh, stale])
        await session.flush()

        def checked(title, next_check_at):
            return Availability(
                addon_id=kitsu_addon.id, title_id=title.id, probe_id="-",
                status=CheckStatus.OK, last_checked=now, next_check_at=next_check_at,
                consecutive_failures=0,
            )

        session.add_all([checked(fresh, now + timedelta(hours=1)),
                         checked(stale, now - timedelta(minutes=1))])
        await session.flush()

        jobs = await due_jobs(session, now)
        # imdb_addon can't take kitsu IDs, `off` is not active, `fresh` isn't due.
        assert [(j.title_id, j.probe_id) for j in jobs] == [
            (popular.id, "kitsu:1:1"), (stale.id, "kitsu:3:1"),
        ]
        assert jobs[0].url == "https://a.example/stream/series/kitsu:1:1.json"
        assert jobs[0].host == "a.example" and jobs[0].popularity_rank == 1

        assert len(await due_jobs(session, now, top_titles=1)) == 1

    db(scenario)
