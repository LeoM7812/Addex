"""Stremio addon manifest parsing.

Reduces a manifest to what the crawler needs: can this addon be asked for streams of a
given (type, id)? Follows the Stremio addon protocol:

- `resources` is a list of either names (`"stream"`) or objects
  (`{"name": "stream", "types": [...], "idPrefixes": [...]}`).
- A bare name inherits the manifest's top-level `types` and `idPrefixes`.
- No `idPrefixes` means the resource accepts any ID of its types.
- Stremio matches IDs by `id.startswith(prefix)`.
"""

from dataclasses import dataclass
from typing import Any, Self
from urllib.parse import quote, urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

MANIFEST_SUFFIX = "/manifest.json"
MAX_MANIFEST_BYTES = 512 * 1024


class ManifestError(Exception):
    """Manifest could not be fetched or is not a valid Stremio manifest."""


# Raw shape, as published by addons. Lenient: extra fields are ignored and only what the
# protocol requires for routing is validated.


class _RawResource(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str
    types: list[str] | None = None
    id_prefixes: list[str] | None = Field(default=None, alias="idPrefixes")


class _RawBehaviorHints(BaseModel):
    model_config = ConfigDict(extra="ignore")

    adult: bool = False
    p2p: bool = False
    configurable: bool = False
    configuration_required: bool = Field(default=False, alias="configurationRequired")


class _RawManifest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    version: str
    name: str
    description: str | None = None
    logo: str | None = None
    types: list[str] = []
    resources: list[str | _RawResource]
    id_prefixes: list[str] | None = Field(default=None, alias="idPrefixes")
    behavior_hints: _RawBehaviorHints = Field(
        default_factory=_RawBehaviorHints, alias="behaviorHints"
    )


@dataclass(frozen=True)
class StreamScope:
    """One `stream` resource declaration: which types and ID prefixes it answers for."""

    types: frozenset[str]
    # None = any ID.
    id_prefixes: tuple[str, ...] | None

    def accepts(self, stremio_type: str, stremio_id: str) -> bool:
        if stremio_type not in self.types:
            return False
        if self.id_prefixes is None:
            return True
        return stremio_id.startswith(self.id_prefixes)

    def to_json(self) -> dict[str, Any]:
        return {
            "types": sorted(self.types),
            "id_prefixes": list(self.id_prefixes) if self.id_prefixes is not None else None,
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> Self:
        prefixes = data.get("id_prefixes")
        return cls(
            types=frozenset(data["types"]),
            id_prefixes=tuple(prefixes) if prefixes is not None else None,
        )


@dataclass(frozen=True)
class Manifest:
    manifest_url: str
    id: str
    name: str
    version: str
    description: str | None
    logo: str | None
    types: tuple[str, ...]
    stream_scopes: tuple[StreamScope, ...]
    p2p: bool
    adult: bool
    configuration_required: bool
    raw: dict[str, Any]

    @property
    def base_url(self) -> str:
        return self.manifest_url.removesuffix(MANIFEST_SUFFIX)

    @property
    def provides_streams(self) -> bool:
        return bool(self.stream_scopes)

    @property
    def install_url(self) -> str:
        """`stremio://` deep link that opens the install dialog in the Stremio app."""
        return "stremio://" + self.manifest_url.split("://", 1)[1]

    @property
    def stream_types(self) -> frozenset[str]:
        return frozenset().union(*(s.types for s in self.stream_scopes))

    def supports_stream(self, stremio_type: str, stremio_id: str) -> bool:
        return any(s.accepts(stremio_type, stremio_id) for s in self.stream_scopes)

    def stream_url(self, stremio_type: str, stremio_id: str) -> str:
        return stream_url(self.base_url, stremio_type, stremio_id)


def stream_url(base_url: str, stremio_type: str, stremio_id: str) -> str:
    return f"{base_url}/stream/{quote(stremio_type, safe='')}/{quote(stremio_id, safe=':')}.json"


def normalize_manifest_url(url: str) -> str:
    """Canonical https manifest URL. Accepts `stremio://` links and URLs missing the
    `/manifest.json` suffix. Keeps any config path segment intact."""
    url = url.strip()
    if url.startswith("stremio://"):
        url = "https://" + url.removeprefix("stremio://")
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise ManifestError(f"not an http(s) URL: {url!r}")
    if parts.query or parts.fragment:
        url = url.split("?", 1)[0].split("#", 1)[0]
    url = url.rstrip("/")
    if not url.endswith(MANIFEST_SUFFIX):
        url += MANIFEST_SUFFIX
    return url


def _stream_scopes(raw: _RawManifest) -> tuple[StreamScope, ...]:
    scopes = []
    for res in raw.resources:
        if isinstance(res, str):
            if res != "stream":
                continue
            types, prefixes = raw.types, raw.id_prefixes
        else:
            if res.name != "stream":
                continue
            types = res.types if res.types is not None else raw.types
            prefixes = res.id_prefixes if res.id_prefixes is not None else raw.id_prefixes
        if not types:
            continue
        # Like stremio-core: an absent idPrefixes accepts any ID, an empty list accepts none.
        scopes.append(
            StreamScope(
                types=frozenset(types),
                id_prefixes=tuple(prefixes) if prefixes is not None else None,
            )
        )
    return tuple(scopes)


def parse_manifest(data: Any, manifest_url: str) -> Manifest:
    url = normalize_manifest_url(manifest_url)
    if not isinstance(data, dict):
        raise ManifestError(f"{url}: manifest is not a JSON object")
    try:
        raw = _RawManifest.model_validate(data)
    except ValidationError as e:
        raise ManifestError(f"{url}: invalid manifest: {e}") from e

    hints = raw.behavior_hints
    return Manifest(
        manifest_url=url,
        id=raw.id,
        name=raw.name,
        version=raw.version,
        description=raw.description,
        logo=raw.logo,
        types=tuple(raw.types),
        stream_scopes=_stream_scopes(raw),
        p2p=hints.p2p,
        adult=hints.adult,
        configuration_required=hints.configuration_required,
        raw=data,
    )


async def fetch_manifest(client: httpx.AsyncClient, url: str, timeout: float = 10.0) -> Manifest:
    url = normalize_manifest_url(url)
    try:
        resp = await client.get(url, timeout=timeout, follow_redirects=True)
    except httpx.HTTPError as e:
        raise ManifestError(f"{url}: {type(e).__name__}: {e}") from e
    if resp.status_code != 200:
        raise ManifestError(f"{url}: HTTP {resp.status_code}")
    if len(resp.content) > MAX_MANIFEST_BYTES:
        raise ManifestError(f"{url}: manifest larger than {MAX_MANIFEST_BYTES} bytes")
    try:
        data = resp.json()
    except ValueError as e:
        raise ManifestError(f"{url}: response is not JSON") from e
    return parse_manifest(data, url)
