# Decisions

Sign-off items for the extraction step.

- **D1 · Places budget vs grid.** The bbox measures 28.7 x 29.6 km. At `tile_km: 2.0` that is 225 tiles x 8 types = 1,800 first-page requests, against a cap of 300. `tile_km` is treated as the quadtree root size, set to 8.0 (16 roots x 8 types = 128 first pages). Saturation splits only dense tiles, through 8 -> 4 -> 2 -> 1 -> 0.5 km. The `MAX_PLACES_REQUESTS` default is 1000, the Enterprise free tier. Expect about 600-1,200 requests; the first live run gives the real number.
- **D2 · `run_log` stays append-only.** Instead of `start_step()` followed by an update in `finish_step()`, a `step()` context manager records `started_at` and writes one row in `finally`.
- **D3 · Sodra source.** `SODRA_URL` comes from the user, copied from the page in a browser. `SODRA_FILE` is a fallback for a manually downloaded zip, with `source_url` = the value of `SODRA_URL` if set, else `file://<abs path>`.
- **D4 · Firecrawl rate** is 10 requests/min by default (free plan), overridable with `FIRECRAWL_RATE_PER_MIN`.
- **D5 · No `firecrawl-py`.** Calling REST through our own client keeps caching, retries, counters and `MockTransport` tests on one code path.
- **D6 · Env key name.** The spec says `GOOGLE_API_KEY`; `.env` originally had `GOOGLE_PLACES=`. The code reads `GOOGLE_API_KEY`. Update: `.env` has since been renamed to `GOOGLE_API_KEY`.
- **D7 · Python 3.13.** The repo already pins it in `.python-version`, `requires-python` and `.venv`, and nothing in the stack needs 3.12.
- **D8 · Cache TTLs.** Places 30 days (ToS-aware: Google allows `place_id` to be cached indefinitely but other content only briefly). Robots 1 day. Firecrawl 30 days. Sodra has no cache.
- **D9 · robots.txt outcomes** (RFC 9309): 2xx -> parse; 4xx -> allow all; 5xx or network failure -> treat as disallow (`robots_disallow`, note `robots_unreachable`).

## Rulings made during implementation

- **R6** Firecrawl client has no `max_requests`; the crawl step enforces `MAX_FIRECRAWL_PAGES` (updated by R24 to count network attempts, retries included). Places client `max_requests` = `MAX_PLACES_REQUESTS`.
- **R9** Sodra validation expects contiguous months from YYYY01 with at least 50,000 rows each, not "exactly one month": the real file is year-to-date (8 months).
- **R16** `raw.places.retrieved_at` is the time the source served the page (`CachedResponse.fetched_at`, naive UTC), stable under caching; `insert_rows` normalises per-row tz-aware values.
- **R17** Rows from pages already fetched always land: on a cap mid-chain and on a later-page error (with its error row).
- **R18** For URLs sharing a dedupe key, prefer https, then bare host, then shortest, then lexicographic.
- **R19** A `websiteUri` with an unparseable or out-of-range port classifies as `no_website`, so one dirty row cannot abort the run.
- **R20** robots.txt 429 (after retries) is treated as unreachable, i.e. disallow, not as a 4xx allow-all.
- **R21** Legal-link ranking: tier 0 `rekvizit`, tier 1 `kontakt|contact`, then the rest.
- **R22** The example report is generated from the recorded-replay demo run and committed at `output/demo-recorded/extract_report.md` with a banner saying it is not a live run.
- **R23** Firecrawl 401 is an `AuthError` (step failed); 403 `UNSUPPORTED_SITE` is per-URL, free and non-retryable, so it becomes an item error row.
- **R24** The page cap counts Firecrawl network attempts including retries (cache hits excluded); the Firecrawl client timeout is 90 s (server-side scrape timeout is 30-60 s).
- **R25** Crawl `run_log.api_requests`/`cache_hits` are Firecrawl only; robots counts go in notes (`robots_requests`, `robots_cache_hits`) and a `robots` row in the report.
- **R26** API keys are optional at settings load; the client factories require them; `report` and `sodra` run without keys; under `all` a missing key records that step as failed (`missing_setting=<NAME>`).
- **R27** Firecrawl 200 responses with `success:false` are not cached, so a transient failure does not stick for 30 days.
