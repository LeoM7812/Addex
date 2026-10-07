# Addex

Open-source index of which [Stremio](https://www.stremio.com/) addons have streams for each title.

Addex only records **whether** an addon answers for a title (`has_streams`, `stream_count`,
`latency_ms`, `last_checked`). It never stores stream URLs, magnets or info hashes. Those
come from the addon itself, after the user installs it.

## Layout

```
packages/core/      shared Python: data model, migrations, manifest parser, Stremio ID helpers
services/crawler/   asyncio + httpx job runner that probes /stream/{type}/{id}.json   (todo)
services/api/       FastAPI: search + the Addex Stremio addon                         (todo)
apps/web/           Next.js front-end                                                 (todo)
registry/           curated list of addon manifest URLs
seeds/              title seeds (anime top 500, IMDb top)                             (todo)
```

## Development

```sh
docker compose up -d                       # postgres + redis
python -m venv .venv && . .venv/Scripts/activate   # or .venv/bin/activate
pip install -e "packages/core[dev]"

cp .env.example .env
cd packages/core && alembic upgrade head && cd ../..

pytest packages/core
addex-manifest --registry registry/addons.yaml   # fetch and check every registered manifest
addex-manifest https://example.com/manifest.json
```

## Data model

| table          | what it holds |
|----------------|---------------|
| `addons`       | one row per registered manifest URL; normalized stream scopes (types + id prefixes) and health |
| `titles`       | movies / series / anime to probe, with a popularity rank that drives refresh frequency |
| `title_ids`    | external IDs per title (`imdb`, `kitsu`, `mal`, ...). One title can have several |
| `availability` | latest probe result per (addon, title): status, has_streams, stream_count, latency, next_check_at |

Series are probed through a representative episode (`tt0944947:1:1`, `kitsu:1376:1`), because
Stremio addons only answer stream requests for episodes, not for the whole series.
