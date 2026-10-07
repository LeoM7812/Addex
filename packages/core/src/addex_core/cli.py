"""addex: maintenance commands.

  addex manifest URL... [--registry FILE]   fetch manifests and show how they'd be crawled
  addex registry sync [--registry FILE]     upsert registry addons into the database
  addex seed anime [--limit N]              load the most popular anime from Kitsu
  addex seed imdb [--limit N] [--type T]    load the most popular movies/series from Cinemeta
  addex link anime                          group Kitsu entries under their IMDb title
"""

import argparse
import asyncio
import sys
from pathlib import Path

import httpx

from addex_core import animelists, cinemeta
from addex_core.linking import link_anime
from addex_core.db import make_engine, make_sessionmaker
from addex_core.kitsu import fetch_top_anime
from addex_core.manifest import Manifest, ManifestError
from addex_core.registry import fetch_all, load_registry, sync_registry
from addex_core.titles import TitleSeed, upsert_titles

DEFAULT_REGISTRY = Path("registry/addons.yaml")
USER_AGENT = "addex/0.1"


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(headers={"User-Agent": USER_AGENT})


def _describe(m: Manifest) -> str:
    lines = [f"{m.name} {m.version}  ({m.id})", f"  {m.manifest_url}"]
    if not m.provides_streams:
        lines.append("  no stream resource: will not be crawled")
    for s in m.stream_scopes:
        prefixes = ", ".join(s.id_prefixes) if s.id_prefixes else "any id"
        lines.append(f"  stream: {', '.join(sorted(s.types))}  [{prefixes}]")
    flags = [f for f, on in (("p2p", m.p2p), ("adult", m.adult),
                             ("needs config", m.configuration_required)) if on]
    if flags:
        lines.append(f"  flags: {', '.join(flags)}")
    return "\n".join(lines)


async def cmd_manifest(args: argparse.Namespace) -> int:
    urls = list(args.urls) + (load_registry(args.registry) if args.registry else [])
    if not urls:
        print("give manifest URLs or --registry", file=sys.stderr)
        return 2
    async with _client() as client:
        results = await fetch_all(client, urls)
    for result in results:
        print(f"FAIL {result}\n" if isinstance(result, ManifestError) else _describe(result) + "\n")
    failures = sum(isinstance(r, ManifestError) for r in results)
    print(f"{len(urls) - failures}/{len(urls)} manifests OK")
    return 1 if failures else 0


async def cmd_registry_sync(args: argparse.Namespace) -> int:
    urls = load_registry(args.registry)
    async with _client() as client:
        results = await fetch_all(client, urls)
    engine = make_engine()
    try:
        async with make_sessionmaker(engine).begin() as session:
            report = await sync_registry(session, urls, results)
    finally:
        await engine.dispose()
    for url, error in report.failed:
        print(f"FAIL {error}")
    for url in report.disabled:
        print(f"disabled (not in registry): {url}")
    print(
        f"{len(report.added)} added, {len(report.updated)} updated, "
        f"{len(report.failed)} failed, {len(report.disabled)} disabled"
    )
    return 1 if report.failed else 0


async def cmd_seed_anime(args: argparse.Namespace) -> int:
    async with _client() as client:
        seeds = await fetch_top_anime(client, limit=args.limit)
    return await _store_seeds(seeds)


async def cmd_seed_imdb(args: argparse.Namespace) -> int:
    types = ["movie", "series"] if args.type == "both" else [args.type]
    seeds = []
    async with _client() as client:
        for stremio_type in types:
            seeds += await cinemeta.fetch_top(client, stremio_type, limit=args.limit)
    return await _store_seeds(seeds)


async def cmd_link_anime(args: argparse.Namespace) -> int:
    async with _client() as client:
        mapping = await animelists.fetch_kitsu_to_imdb(client)
        sem = asyncio.Semaphore(4)

        async def fetch_one(imdb_id: str, types: tuple[str, ...]):
            async with sem:
                return imdb_id, await cinemeta.fetch_meta(client, imdb_id, types)

        async def fetch_parents(wanted: dict[str, tuple[str, ...]]):
            print(f"fetching {len(wanted)} parent titles from Cinemeta...")
            return dict(await asyncio.gather(*(fetch_one(i, t) for i, t in wanted.items())))

        engine = make_engine()
        try:
            async with make_sessionmaker(engine).begin() as session:
                report = await link_anime(session, mapping, fetch_parents)
        finally:
            await engine.dispose()
    if report.parents_missing:
        print(f"not on Cinemeta: {', '.join(report.parents_missing)}")
    print(f"{report.linked} linked ({report.changed} changed), {report.unmapped} without "
          f"IMDb mapping, {report.parents_created} parents created")
    return 0


async def _store_seeds(seeds: list[TitleSeed]) -> int:
    engine = make_engine()
    try:
        async with make_sessionmaker(engine).begin() as session:
            report = await upsert_titles(session, seeds)
    finally:
        await engine.dispose()
    for scheme, value, owner in report.id_conflicts:
        print(f"skipped {scheme}:{value}, already attached to title {owner}")
    print(f"{len(seeds)} fetched: {report.added} added, {report.updated} updated")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(prog="addex", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(required=True, metavar="command")

    p = sub.add_parser("manifest", help="fetch manifests and show how they'd be crawled")
    p.add_argument("urls", nargs="*", help="manifest URLs")
    p.add_argument("--registry", type=Path, help="also check every URL in this registry")
    p.set_defaults(func=cmd_manifest)

    registry = sub.add_parser("registry", help="addon registry").add_subparsers(
        required=True, metavar="action"
    )
    p = registry.add_parser("sync", help="upsert registry addons into the database")
    p.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    p.set_defaults(func=cmd_registry_sync)

    seed = sub.add_parser("seed", help="load titles").add_subparsers(
        required=True, metavar="source"
    )
    p = seed.add_parser("anime", help="most popular anime from Kitsu")
    p.add_argument("--limit", type=int, default=500)
    p.set_defaults(func=cmd_seed_anime)
    p = seed.add_parser("imdb", help="most popular movies and series (Cinemeta, IMDb IDs)")
    p.add_argument("--limit", type=int, default=500, help="titles per type")
    p.add_argument("--type", choices=["movie", "series", "both"], default="both")
    p.set_defaults(func=cmd_seed_imdb)

    link = sub.add_parser("link", help="link titles across sources").add_subparsers(
        required=True, metavar="what"
    )
    p = link.add_parser("anime", help="group Kitsu entries under their IMDb title")
    p.set_defaults(func=cmd_link_anime)

    args = parser.parse_args()
    # psycopg's async mode can't run on Windows' default Proactor event loop.
    loop_factory = asyncio.SelectorEventLoop if sys.platform == "win32" else None
    raise SystemExit(asyncio.run(args.func(args), loop_factory=loop_factory))


if __name__ == "__main__":
    main()
