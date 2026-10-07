"""addex-manifest: fetch manifests and print what the crawler would do with them."""

import argparse
import asyncio
from pathlib import Path

import httpx

from addex_core.manifest import Manifest, ManifestError, fetch_manifest
from addex_core.registry import load_registry


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


async def _run(urls: list[str]) -> int:
    failures = 0
    async with httpx.AsyncClient(headers={"User-Agent": "addex/0.1"}) as client:
        results = await asyncio.gather(
            *(fetch_manifest(client, u) for u in urls), return_exceptions=True
        )
    for url, result in zip(urls, results):
        if isinstance(result, ManifestError):
            failures += 1
            print(f"FAIL {result}\n")
        elif isinstance(result, BaseException):
            raise result
        else:
            print(_describe(result) + "\n")
    print(f"{len(urls) - failures}/{len(urls)} manifests OK")
    return 1 if failures else 0


def main() -> None:
    parser = argparse.ArgumentParser(prog="addex-manifest", description=__doc__)
    parser.add_argument("urls", nargs="*", help="manifest URLs")
    parser.add_argument("--registry", type=Path, help="registry YAML file")
    args = parser.parse_args()

    urls = list(args.urls)
    if args.registry:
        urls += load_registry(args.registry)
    if not urls:
        parser.error("give manifest URLs or --registry")
    raise SystemExit(asyncio.run(_run(urls)))


if __name__ == "__main__":
    main()
