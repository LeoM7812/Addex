# crawler

Probes `/stream/{type}/{id}.json` on every active addon for every title it can handle,
and records only the outcome in `availability` (status, playable stream count, latency).

```sh
pip install -e packages/core -e "services/crawler[dev]"

addex-crawler schedule --once --top 20   # enqueue due pairs for the 20 most popular titles
addex-crawler work --until-empty         # drain the queues, then exit
addex-crawler run                        # long-running: schedule every 60 s + work
addex-crawler status                     # queue sizes and per-addon results
```

## How it works

- **Scheduler** (`scheduler.py`): for each active addon x title, picks the first title ID the
  addon's stream scopes accept (Kitsu first for anime, IMDb first otherwise) and enqueues a
  job if the pair was never checked or is past `next_check_at`. Most popular titles first.
- **Queue** (`queue.py`): one Redis list per host, so a slow host never blocks the others.
  A per-pair lock key stops the same pair being queued twice; queues are capped per host.
- **Worker** (`worker.py`): one loop per host with its own rate limit (default 1 req/s),
  concurrency cap (2), and circuit breaker (opens after 5 consecutive timeouts/5xx, 30 s
  cooldown doubling to 30 min). A 429 pauses the host for `Retry-After` and requeues the job.
- **Results** (`results.py`): a failed probe updates `status` but keeps the last definitive
  `has_streams`/`stream_count`. Answers are refreshed every 6 h for the top 50 titles,
  12 h up to rank 200, 1 day up to 1000, 3 days otherwise; failures retry after 15 min,
  doubling up to that TTL.

Streams count only if they carry a source key (`url`, `infoHash`, `ytId`, `externalUrl`, ...);
the key's value is never read or stored.

## Limits

Rate limits and circuit breakers live in the worker process: run a single worker, or
move them to Redis before scaling out.
