# api

FastAPI app serving the JSON API, Addex's own Stremio addon, and its install/configure page.

```sh
pip install -e packages/core -e "services/api[dev]"
addex-api --port 8000          # page at http://127.0.0.1:8000, API docs at /docs
```

## JSON API

- `GET /api/search?q=attack&limit=20`: top-level titles whose name or aliases (or any
  grouped entry's) match. Substring match plus trigram similarity for typos; exact and
  prefix matches first, then popularity. `addon_count` counts active addons with streams.
- `GET /api/titles/{id}`: a title, its grouped per-season entries, and per active addon
  the latest result for each entry. A child ID returns its parent's group.
- `GET /api/addons`: active stream addons with measured coverage, best first.

## Stremio addon

Install from the page at `/`, or add `https://<host>/manifest.json` in Stremio.

- **Streams**: for a movie or episode, one `Install <addon>` entry per indexed addon that
  has it. `externalUrl` is the Stremio Web install page (`web.stremio.com/#/addons?addon=...`);
  a `stremio://` link would be mangled, since Stremio opens `externalUrl` in the system
  browser. Never a playable source.
- **Addon catalogs** (`addon_catalog`): *Addex: most coverage* and *Addex: anime* list the
  indexed addons ranked by measured coverage, installable from Stremio's own Addons screen.
- **Configuration**: the page (also behind Stremio's *Configure* button) lets users tick the
  addons they already have and hide P2P addons. Settings travel in the install URL
  (`/{config}/manifest.json`, base64url JSON), so there are no accounts.
- **Caching**: `cacheMaxAge` / `staleRevalidate` / `staleError` are sent in the body and as
  `Cache-Control`, like the official SDK. Empty stream answers are cached for 60 s, since a
  check was usually just queued.

Addons that are not `active` (disabled, broken, needing configuration) are never shown.
P2P means the manifest declares it or the crawler has seen the addon return torrents.

Behind a reverse proxy, run with `--behind-proxy` so absolute URLs (logo, background) use
the public https host. `ADDEX_CONTACT_EMAIL` adds a Report contact to the manifest.

`scripts/make_images.py` regenerates `static/logo.png` and `static/background.png`.
