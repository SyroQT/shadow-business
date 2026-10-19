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
