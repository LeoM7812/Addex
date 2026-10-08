# Addex

Open-source index of which [Stremio](https://www.stremio.com/) addons have streams for each title.

Addex only records **whether** an addon answers for a title (`has_streams`, `stream_count`,
`latency_ms`, `last_checked`). It never stores stream URLs, magnets or info hashes. Those
come from the addon itself, after the user installs it.

## Layout

```
packages/core/      shared Python: data model, migrations, manifest parser, registry sync,
                    title seeds, Stremio ID helpers, `addex` CLI
services/crawler/   asyncio + httpx job runner that probes /stream/{type}/{id}.json
services/api/       FastAPI: search + the Addex Stremio addon
apps/web/           Next.js front-end                                                 (todo)
registry/           curated list of addon manifest URLs
deploy/             production docker-compose + Caddy; see deploy/README.md
```

## Development

```sh
docker compose up -d                       # postgres (port 5433) + redis
python -m venv .venv && . .venv/Scripts/activate   # or .venv/bin/activate
pip install -e "packages/core[dev]" -e "services/crawler[dev]" -e "services/api[dev]"

cp .env.example .env
cd packages/core && alembic upgrade head && cd ../..

pytest                                     # DB/Redis tests use addex_test and redis db 15, skipped if unreachable

addex manifest https://example.com/manifest.json   # inspect a manifest, no DB needed
addex registry sync                        # registry/addons.yaml -> addons table
addex seed anime --limit 500               # most popular anime from Kitsu, with MAL/AniList IDs
addex seed imdb --limit 500                # most popular movies + series from Cinemeta (IMDb IDs)
addex link anime                           # group Kitsu entries under their IMDb title (run after seeding)
addex publish https://<host>/manifest.json # list Addex in Stremio's community catalog (public https only)
addex-crawler run                          # see services/crawler/README.md
addex-api                                  # see services/api/README.md
```

`registry/addons.yaml` is the source of truth for tracked addons: removing an entry marks the
addon `disabled` on the next sync, adding it back re-enables it.

## Data model

| table          | what it holds |
|----------------|---------------|
| `addons`       | one row per registered manifest URL; normalized stream scopes (types + id prefixes) and health |
| `titles`       | movies / series / anime to probe, with a popularity rank that drives refresh frequency |
|                | Kitsu anime entries (one per season) point to their IMDb series/movie via `parent_id`; search shows parents |
| `title_ids`    | external IDs per title (`imdb`, `kitsu`, `mal`, ...). One title can have several |
| `availability` | latest probe result per (addon, title): status, has_streams, stream_count, latency, next_check_at |

Series are probed through a representative episode (`tt0944947:1:1`, `kitsu:1376:1`), because
Stremio addons only answer stream requests for episodes, not for the whole series.
