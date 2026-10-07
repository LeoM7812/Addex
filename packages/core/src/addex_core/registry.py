"""Addon registry: the YAML list of manifest URLs, and its sync into the `addons` table.

The YAML file is the source of truth for which addons Addex tracks. Removing (or
commenting out) an entry disables the addon; adding it back re-enables it.
"""

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import httpx
import yaml
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from addex_core.manifest import Manifest, ManifestError, fetch_manifest, normalize_manifest_url
from addex_core.models import Addon, AddonStatus


def load_registry(path: Path) -> list[str]:
    """Normalized, de-duplicated manifest URLs from the registry YAML."""
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    urls: list[str] = []
    for entry in data.get("addons") or []:
        url = normalize_manifest_url(entry["url"] if isinstance(entry, dict) else entry)
        if url not in urls:
            urls.append(url)
    return urls


async def fetch_all(
    client: httpx.AsyncClient, urls: list[str], concurrency: int = 10
) -> list[Manifest | ManifestError]:
    sem = asyncio.Semaphore(concurrency)

    async def one(url: str) -> Manifest | ManifestError:
        async with sem:
            try:
                return await fetch_manifest(client, url)
            except ManifestError as e:
                return e

    return await asyncio.gather(*(one(u) for u in urls))


def status_for(m: Manifest) -> AddonStatus:
    if not m.provides_streams:
        return AddonStatus.NO_STREAMS
    if m.configuration_required:
        return AddonStatus.NEEDS_CONFIG
    return AddonStatus.ACTIVE


def apply_manifest(addon: Addon, m: Manifest, now: datetime) -> None:
    addon.base_url = m.base_url
    addon.manifest_id = m.id
    addon.name = m.name
    addon.version = m.version
    addon.description = m.description
    addon.logo = m.logo
    addon.stream_scopes = [s.to_json() for s in m.stream_scopes]
    addon.p2p = m.p2p
    addon.adult = m.adult
    addon.manifest = m.raw
    addon.status = status_for(m)
    addon.last_fetched_at = now
    addon.last_error = None


@dataclass
class SyncReport:
    added: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    # Fetch failed. Existing addons are marked broken; new ones are not inserted, since
    # there is no manifest to fill the row from.
    failed: list[tuple[str, str]] = field(default_factory=list)
    disabled: list[str] = field(default_factory=list)


async def sync_registry(
    session: AsyncSession, urls: list[str], results: list[Manifest | ManifestError]
) -> SyncReport:
    """Upsert `addons` from fetched manifests. Does not commit."""
    now = datetime.now(UTC)
    report = SyncReport()
    existing = {a.manifest_url: a for a in (await session.scalars(select(Addon))).all()}

    for url, result in zip(urls, results, strict=True):
        addon = existing.get(url)
        if isinstance(result, ManifestError):
            report.failed.append((url, str(result)))
            if addon is not None:
                addon.status = AddonStatus.BROKEN
                addon.last_error = str(result)
            continue
        if addon is None:
            addon = Addon(manifest_url=url)
            session.add(addon)
            report.added.append(url)
        else:
            report.updated.append(url)
        apply_manifest(addon, result, now)

    listed = set(urls)
    for url, addon in existing.items():
        if url not in listed and addon.status != AddonStatus.DISABLED:
            addon.status = AddonStatus.DISABLED
            report.disabled.append(url)

    await session.flush()
    return report
