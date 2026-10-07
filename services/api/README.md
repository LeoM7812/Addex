# api

FastAPI app serving the JSON API for the front-end and Addex's own Stremio addon.

```sh
pip install -e packages/core -e "services/api[dev]"
addex-api --port 8000          # docs at http://127.0.0.1:8000/docs
```

## JSON API

- `GET /api/search?q=attack&limit=20`: top-level titles whose name or aliases (or any
  grouped entry's) match. Substring match plus trigram similarity for typos; exact and
  prefix matches first, then popularity. `addon_count` counts active addons with streams.
- `GET /api/titles/{id}`: a title, its grouped per-season entries, and per active addon
  the latest result for each entry. A child ID returns its parent's group.

## Stremio addon

Install `http://<host>/manifest.json` in Stremio. For a movie or episode it lists every
indexed addon that has the title, as `Install <addon>` entries whose `externalUrl` is the
addon's `stremio://` install link. It never returns a playable source.

Addons that are not `active` (disabled, broken, needing configuration) are never shown.
