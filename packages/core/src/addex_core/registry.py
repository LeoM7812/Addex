from pathlib import Path

import yaml

from addex_core.manifest import normalize_manifest_url


def load_registry(path: Path) -> list[str]:
    """Normalized, de-duplicated manifest URLs from the registry YAML."""
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    urls: list[str] = []
    for entry in data.get("addons") or []:
        url = normalize_manifest_url(entry["url"] if isinstance(entry, dict) else entry)
        if url not in urls:
            urls.append(url)
    return urls
