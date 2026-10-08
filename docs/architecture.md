# Architecture

Addex is three Python packages in one repository, backed by Postgres and Redis:

| package | runs as | job |
|---|---|---|
| `packages/core` (`addex_core`) | library + `addex` CLI | data model and migrations, manifest parser, registry sync, title seeds and linking, Stremio ID helpers |
| `services/crawler` (`addex_crawler`) | `addex-crawler run` | decides what to check, probes addons, records results |
| `services/api` (`addex_api`) | `addex-api` | JSON API, the Stremio addon, the install/configure page |

## Deployment

Everything runs from one Docker Compose file on a single server. Only Caddy is reachable
from outside.

```mermaid
flowchart TB
    internet(["Internet"])

    subgraph vm ["Server (Docker Compose)"]
        caddy["caddy<br/>HTTPS, Let's Encrypt"]
        api["api<br/>addex-api --behind-proxy"]
        crawler["crawler<br/>addex-crawler run"]
        migrate["migrate<br/>alembic upgrade head<br/>(runs once, then exits)"]
        pg[("postgres")]
        redis[("redis")]
    end

    internet -- ":80 / :443" --> caddy
    caddy --> api
    api --> pg
    api --> redis
    crawler --> pg
    crawler --> redis
    migrate --> pg
    crawler -- "outbound only" --> ext(["addons, Cinemeta, Kitsu,<br/>anime-lists"])
```

`api` and `crawler` start only after `migrate` has finished, so the schema is always
current. Redis snapshots every minute, so queued work survives a restart.

## What happens when someone opens a title

The Stremio addon answers from what is already known and, at the same time, asks the
crawler to check the title. Nothing waits on a third-party addon while the viewer waits.

```mermaid
sequenceDiagram
    autonumber
    actor V as Viewer
    participant S as Stremio app
    participant A as Addex API
    participant R as Redis
    participant C as Crawler
    participant M as Cinemeta, Kitsu, anime-lists
    participant X as Indexed addons
    participant P as Postgres

    V->>S: opens Seven Samurai
    S->>A: GET /stream/movie/tt0047478.json
    A->>P: results for this title and its group?
    P-->>A: nothing yet
    A->>R: note demand (deduplicated for 10 min)
    A-->>S: no entries, cacheMaxAge 60 s
    C->>R: demand loop pops the note
    C->>M: unknown title, fetch its metadata
    C->>P: create the title (anime gets linked to its IMDb parent)
    C->>R: queue its due probes at the front of each host queue
    C->>X: GET /stream/movie/tt0047478.json (one per addon)
    X-->>C: stream list, counted then dropped
    C->>P: has_streams, stream_count, latency_ms, next_check_at
    V->>S: reopens a minute later
    S->>A: GET /stream/movie/tt0047478.json
    A->>P: results
    A-->>S: Install TorrentsDB · 50 streams · confirmed 1 min ago
```

Safeguards on this path:

- A title opened many times is noted once per 10 minutes.
- New titles are capped at 30 a minute, so made-up IDs can't turn into a flood of lookups.
- IDs that no metadata source knows are remembered for a day.
- Jobs are queued only after the title is committed, so a worker never sees a title that
  isn't in the database yet.
- If Redis is down the API still answers; only the on-demand check is skipped.

## Crawler

```mermaid
flowchart TB
    sched["Scheduler<br/>every 60 s: pairs never checked<br/>or past next_check_at,<br/>most popular titles first"]
    demand["Demand loop<br/>titles people just opened"]

    subgraph redis ["Redis"]
        q1["addex:jobs:torrentsdb.com"]
        q2["addex:jobs:addon.peerflix.mov"]
        locks["addex:lock:addon:title<br/>at most one job per pair"]
    end

    subgraph workers ["One loop per host"]
        w1["rate limiter → circuit breaker → probe"]
        w2["rate limiter → circuit breaker → probe"]
    end

    pg[("Postgres<br/>availability")]

    sched -- "back of the queue,<br/>capped per host" --> q1 & q2
    demand -- "front of the queue,<br/>not capped" --> q1 & q2
    q1 --> w1
    q2 --> w2
    w1 & w2 -- "result + next check time" --> pg
```

**Which ID to probe with.** A title can have several IDs (IMDb, Kitsu, MAL, AniList). For
each addon the scheduler picks the first one the addon accepts, preferring Kitsu for anime
and IMDb otherwise. Series are probed through their first episode (`tt0944947:1:1`,
`kitsu:1376:1`) because addons only answer for episodes.

**One queue per host.** A slow or broken host only delays its own queue. Each host gets:

- a **rate limiter**: one request per second by default. Every `429` doubles the interval
  (up to one a minute); twenty good answers in a row shrink it by 20% again.
- a **circuit breaker** for hosts that are down:

```mermaid
stateDiagram-v2
    [*] --> Closed
    Closed --> Open: 5 timeouts or 5xx in a row
    Open --> HalfOpen: cooldown over (30 s, doubling up to 30 min)
    HalfOpen --> Closed: next request succeeds
    HalfOpen --> Open: next request fails
    Closed --> Paused: 429 Too Many Requests
    Paused --> Closed: Retry-After elapsed
```

A 404 or a malformed body counts as a bad answer for that title, not as the host being
down.

**What a probe records.** Status (`ok`, `empty`, `timeout`, `http_error`, `invalid`,
`error`), the number of *playable* streams, latency and HTTP status. A stream only counts
if it has a source key (`url`, `infoHash`, `ytId`, `externalUrl`, ...): placeholder entries
such as "configure this addon" are ignored. Whether any stream is a torrent is noted on
the addon (`p2p_observed`), because many torrent addons don't declare it. Only key presence
is checked; the values are never read.

**A failure doesn't erase an answer.** A timeout updates `status` but keeps the last
`has_streams` and `stream_count`, since it says nothing about the title.

**When to check again.**

| title popularity rank | answered | failed |
|---|---|---|
| top 50 | 6 h | 15 min, doubling, up to the answered interval |
| 51 – 200 | 12 h | 〃 |
| 201 – 1000 | 1 day | 〃 |
| unranked | 3 days | 〃 |

## Titles

Seeds come from two sources with different shapes: Kitsu lists anime per season, IMDb
(through Cinemeta) per series. Rather than merging rows, each Kitsu entry points to its
IMDb title through `parent_id`, using the
[Fribb/anime-lists](https://github.com/Fribb/anime-lists) mapping.

```mermaid
flowchart LR
    p["Attack on Titan<br/>imdb tt2560140<br/>(search shows this)"]
    s1["Attack on Titan<br/>kitsu 7442"] --> p
    s2["Attack on Titan Season 2<br/>kitsu 8671"] --> p
    s3["Attack on Titan Season 3<br/>kitsu 13569"] --> p
    s3b["Season 3 Part 2<br/>kitsu 41982"] --> p
    fs["The Final Season<br/>kitsu 42422"] --> p
    fs2["The Final Season Part 2<br/>kitsu 44240"] --> p
```

So each season keeps being probed with its own Kitsu ID, IMDb-only addons still get the
series, the two seeds never overwrite each other, and search and the addon show one
result with everything aggregated.

Titles opened in Stremio are created from whichever ID the client sends: IMDb through
Cinemeta, Kitsu through Kitsu, and MyAnimeList through its Kitsu entry (anime-lists maps
MAL to Kitsu for about two thirds of MAL entries, which covers the commonly watched ones).
A MAL request therefore lands in the same group as the Kitsu and IMDb entries. Search matches names and aliases (romaji, English,
Japanese) of the parent or any child, with trigram similarity for typos.

## Data model

```mermaid
erDiagram
    ADDONS ||--o{ AVAILABILITY : "probed for"
    TITLES ||--o{ AVAILABILITY : "checked on"
    TITLES ||--o{ TITLE_IDS : "known as"
    TITLES |o--o{ TITLES : "groups seasons of"

    ADDONS {
        int id PK
        text manifest_url UK
        text base_url
        jsonb stream_scopes "types + idPrefixes per stream resource"
        bool p2p "declared by the manifest"
        bool p2p_observed "seen returning torrents"
        enum status "active, no_streams, needs_config, broken, disabled"
        jsonb manifest "as last fetched"
    }
    TITLES {
        int id PK
        text type "movie or series"
        text name
        text_array aliases
        int year
        bool is_anime
        int popularity_rank "drives refresh frequency"
        int parent_id FK "Kitsu season to IMDb series"
    }
    TITLE_IDS {
        int title_id PK, FK
        enum scheme PK "imdb, kitsu, mal, anilist, tmdb"
        text value
    }
    AVAILABILITY {
        int addon_id PK, FK
        int title_id PK, FK
        text probe_id "exact ID sent, e.g. kitsu:7442:1"
        enum status
        bool has_streams "last definitive answer"
        int stream_count "playable streams"
        int latency_ms
        timestamptz last_checked
        timestamptz last_answered
        timestamptz next_check_at
        int consecutive_failures
    }
```

Migrations live in `packages/core/migrations` (Alembic); `alembic check` confirms they
match the models.

## Registry

[`registry/addons.yaml`](../registry/addons.yaml) is the list of addons Addex tracks.
`addex registry sync` fetches every manifest and sets each addon's status:

- `active`: crawled.
- `no_streams`: catalog/metadata only.
- `needs_config`: the manifest requires configuration.
- `broken`: the manifest can't be fetched; the last good copy is kept.
- `disabled`: removed from the registry. Adding it back re-enables it.

Only `active` addons are ever shown. The file also records why candidates were left out:
some need configuration, one forbids unauthorised clients, and some block datacenter IPs.

## Testing

`pytest` from the repository root runs every package's tests. Database and Redis tests
use a separate `addex_test` database and Redis db 15, wrap each test in a transaction that
is rolled back, and are skipped if the services aren't running. HTTP is mocked with
`httpx.MockTransport`; nothing in the test suite touches the network.
