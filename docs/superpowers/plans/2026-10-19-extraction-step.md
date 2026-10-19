# Extraction Step Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land Google Places, Firecrawl homepage/legal pages and the Sodra monthly export unmodified into DuckDB `raw.*`, stamped per `run_id`, under budget caps, with a data-quality report.

**Architecture:** One shared HTTP layer (`src/cache.py`: httpx client + on-disk cache + token bucket + tenacity retries + counters) is used by all three extractors. Each extractor is a module in `src/extract/` with pure, fixture-tested functions (tiling, URL classification, link selection, header mapping) plus a thin `run(...)` that writes to DuckDB inside a `step()` context manager that always writes one `raw.run_log` row. `python -m src.extract all` chains `sodra → places → crawl` under one `RunContext`.

**Tech Stack:** Python 3.13 (already pinned in repo, see D7), uv, httpx, duckdb, pyyaml, pydantic-settings, tenacity, pytest. Firecrawl is called over REST v2 with httpx, not `firecrawl-py` (D5).

**Spec:** `docs/specs/extraction-spec.md`

---

## Findings that change the spec (resolved open questions, §5)

Checked on 2026-10-19 against current docs and the Sodra file already in `data/raw/sodra/monthly-2026.csv.zip`.

| # | Finding | Consequence in this plan |
|---|---|---|
| Q1 | Text Search (New): `textQuery` is **required**. `includedType` + `strictTypeFiltering: true` filters to that type. `pageSize` max 20, **max 60 results total (3 pages)**. `locationRestriction.rectangle` is `{low:{latitude,longitude}, high:{latitude,longitude}}`. | `textQuery` = the type with `_` → space (`"hair salon"`), plus `strictTypeFiltering: true`. Saturated = 60 results returned. |
| Q2 | The field mask includes `websiteUri`, `rating`, `priceLevel` and `regularOpeningHours`, which put every request on the **Text Search Enterprise** SKU: 1,000 free events/month per billing account, then $35 per 1,000. `websiteUri` is needed for the crawl, so Enterprise can't be avoided. | README shows cost = `max(0, requests − 1000) × $0.035`. Each page counts as one request. |
| Q3 | Firecrawl v2: `POST https://api.firecrawl.dev/v2/scrape`, body `{url, formats:["markdown","links"], onlyMainContent:false}`. The API returns 200 with `success`, and the page's own status is in `data.metadata.statusCode`. The final URL is in `data.metadata.url` and the requested URL in `sourceURL`. `links` is a **list of URL strings with no anchor text**. The free plan allows **10 scrapes/min** and 1,000 credits/month, at 1 credit per basic scrape. Firecrawl reports `robotsBlocked` in batch jobs, so it does apply robots itself; we keep our own check anyway. | Limiter default for Firecrawl is 10/min, not 2 rps (D4). The anchor text for EXT-08 comes from `[text](url)` pairs in the markdown. At 10/min, 600 pages take about 60 min. |
| Q4 | `atvira.sodra.lt` sits behind a **Cloudflare JS challenge**: `curl` gets HTTP 403 "Just a moment...", for both the index page and the zip. I could not inspect the page, so no URL is guessed. The file in hand, `monthly-2026.csv.zip`, is UTF-8 with a BOM, `;`-delimited and `"`-quoted, with 13 columns and one header row. | EXT-09 supports `SODRA_URL` (download, with challenge detection that gives a clear error) **and** `SODRA_FILE` (a local zip downloaded by hand in a browser). The user must copy the real link from the page in a browser (D3). |
| S1 | The "monthly" file is **year-to-date**: 8 distinct months (202601–202608), 835,029 rows, about 104–106k per month. | The validation "exactly one distinct `month`" is **replaced**: months must be contiguous from `YYYY01`, and every month must have ≥ 50,000 rows. `data_month` is set per row. |
| S2 | Activity codes are on **EVRK 2.1**. `96.02` and `45.2` match nothing. Hairdressing is `96.21.00`, beauty `96.22.00`, and motor-vehicle repair is **`95.31.00`** (2,069 firms). `56.1`/`56.3` still match (56.11 restaurants, 56.12 mobile, 56.30 bars). | `categories.yaml` uses `["56.11","56.30"]`, `["96.21","96.22"]`, `["95.31"]`. |
| S3 | 100% of non-empty `jarCode` values are 9 digits, and every non-empty `numInsured` parses as an integer. 3,341 rows in 202608 have an empty `ecoActCodeStr`. | Thresholds stay as specified. |

## Decisions needing your sign-off (also recorded in `DECISIONS.md`)

- **D1 · Places budget vs grid (blocks EXT-05's "full run within budget").** The bbox measures 28.7 × 29.6 km. At `tile_km: 2.0` that is 225 tiles × 8 types = **1,800 first-page requests**, against a cap of 300. Plan: treat `tile_km` as the **quadtree root size, set to 8.0** (16 roots × 8 types = 128 first pages). Saturation then splits only the dense tiles, through 8 → 4 → 2 → 1 → 0.5 km. Set the `MAX_PLACES_REQUESTS` default to **1000**, the Enterprise free tier. Expect about 600–1,200 requests; the first live run will give the real number for the README.
- **D2 · `run_log` stays append-only.** Instead of `start_step()` followed by an update in `finish_step()`, a `step()` context manager records `started_at` and writes **one** row in `finally`.
- **D3 · Sodra source.** `SODRA_URL` comes from the user, copied from the page in a browser. `SODRA_FILE` is a fallback for a manually downloaded zip, with `source_url` = the value of `SODRA_URL` if set, else `file://<abs path>`.
- **D4 · Firecrawl rate** = 10 requests/min by default (free plan), overridable with `FIRECRAWL_RATE_PER_MIN`.
- **D5 · No `firecrawl-py`.** Calling REST through our own client keeps the caching, retries, counters and `MockTransport` tests on one code path.
- **D6 · Env key name.** The spec says `GOOGLE_API_KEY`, but your `.env` currently has `GOOGLE_PLACES=`. The plan reads `GOOGLE_API_KEY`, so rename it in `.env`. `FIRECRAWL_API_KEY` is not in `.env` yet.
- **D7 · Python 3.13.** The repo already pins it in `.python-version`, `requires-python` and `.venv`, and nothing in the stack needs 3.12.
- **D8 · Places cache TTL is 30 days** (ToS-aware: Google allows `place_id` to be cached indefinitely but other content only briefly). Robots cache TTL is 1 day. Firecrawl is 30 days. Sodra has no cache.
- **D9 · robots.txt outcomes** (RFC 9309): 2xx → parse; 4xx → allow all; 5xx or network failure → treat as disallow (`robots_disallow`, note `robots_unreachable`).

## Kick-off & execution order

**Step 0. Housekeeping (one commit, before Task 1).**
- Create the branch `extract` from `master`.
- Commit the spec, this plan, `scripts/record_fixtures.py`, `tests/replay.py`, `.gitignore`, and the `httpx` addition as `EXT-00: spec, plan and demo fixture recorder`.
- In `.env`: rename `GOOGLE_PLACES` → `GOOGLE_API_KEY` (D6), add `FIRECRAWL_API_KEY`, and set `SODRA_FILE=data/raw/sodra/monthly-2026.csv.zip`.
- Run `scripts/record_fixtures.py firecrawl` (10 credits) so that Tasks 7–8 have real Firecrawl response shapes.

**Wave 1. Foundation, offline, no spend.** Task 1 → Task 2 → Task 3, strictly in order. Every later task depends on `RunContext`, `step()`, `insert_rows()` and `SourceClient`, so these interfaces must be stable before anything else starts.

**Wave 2. Sodra end to end, offline, against the real 835k-row file.** Task 9 → Task 10. Sodra comes first because it needs no API spend and lands real data. It also proves that `step()`/`run_log` and the append-only writer work at full scale before the API-driven steps rely on them.

**Wave 3. Places.** Task 4, then Task 5.
- Build Task 4 against committed fixtures, plus one `recorded` replay test over the 10 Places calls already saved (two of the saved searches show pagination; the Old Town restaurant one is saturated at 60).
- The live smoke run is 1–3 requests.
- **Gate:** Task 5's full Vilnius run (about 600–1,200 requests) needs explicit user approval before it runs.

**Wave 4. Crawl.** Task 6 → Task 7 → Task 8. Task 6 can start right after Wave 1, since it only reads `raw.places`; the Task 4 fixtures are enough for it. Live spend is 5 credits in Task 7, plus however many pages the full run needs.

**Wave 5. Glue.** Task 11 → Task 12. The full run behind the committed example report is the second spend gate: up to 600 Firecrawl credits.

**Review points.** Stop for user review after Wave 1, where the interfaces get locked in, and before each spend gate.

## Global Constraints

- DB `data/leads.duckdb`, schema `raw`. Single writer, steps sequential.
- `run_month` = `YYYY-MM` from env `RUN_MONTH`, default the current UTC month. `run_id` = `{run_month}_{UTC yyyymmddTHHMMSS}`, e.g. `2026-10_20261019T141502`.
- Raw tables are append-only (no UPDATE/DELETE anywhere). Every row has `run_id` and `retrieved_at` (UTC TIMESTAMP).
- Payloads are stored as-is: `JSON` for Places and links, `VARCHAR` for markdown and Sodra fields.
- Secrets come only from `.env` (`GOOGLE_API_KEY`, `FIRECRAWL_API_KEY`). They are never logged, never part of a cache key, never in a URL, and masked as `abcd…` (first 4 characters) in printed config.
- Budget: `MAX_PLACES_REQUESTS` (default 1000, D1), `MAX_FIRECRAWL_PAGES` (default 600). Hitting a cap stops that extractor cleanly and sets `status='capped'`.
- Retries: 429/5xx/timeouts, exponential backoff with jitter, max 3 attempts. Any other 4xx is not retried.
- Item failures become error rows. Whole-source failure (401/403 from the API, a Sodra validation failure, an unhandled exception) → `status='failed'` and a non-zero exit.
- Logs are JSON lines on stdout: `{"ts","event","step","run_id",...counts,"duration_ms"}`. No payloads.
- No network in tests. Fixtures live in `tests/fixtures/`. Live tests are marked `@pytest.mark.live` and skipped unless `LIVE=1`.
- Two kinds of fixture:
  - **Committed:** small and hand-built, with synthetic ids and names, but the same JSON shape as the real responses. Every default test uses these, so a fresh clone passes `pytest`.
  - **Recorded:** `tests/fixtures/recorded/` is gitignored (Places ToS) and written by `scripts/record_fixtures.py`. Only tests marked `@pytest.mark.recorded` use it, and they skip when the directory is missing.
- `tests/replay.py::replay_transport(*sources)` serves recorded calls by `request_hash`. Once Task 3 exists, it imports `request_hash` from `src.cache` instead of keeping its own copy, and the recorder does the same.
- Determinism: iterate categories, types, tiles and URLs in sorted, stable order.
- Commit messages are prefixed with the task ID, e.g. `EXT-03: ...`.

## Review Focus

1. **Expired page 2 behind a cached page 1.** Page 1 comes from cache with an old `nextPageToken` while page 2 is not cached, so Google returns 400 INVALID_ARGUMENT. Expected: when a non-first page fails with 400, refetch the chain with the cache bypassed once. Test in Task 4: `test_stale_page_token_refetches_chain`.
2. **Bad API key.** A wrong or missing key must not produce 1,800 error rows. Expected: the first 401/403 aborts the step with `status='failed'`. Tests in Tasks 4 and 7: `test_auth_error_fails_step`.
3. **Firecrawl out of credits (HTTP 402).** Expected: stop like a cap (`status='capped'`, note `firecrawl_402`), and give the remaining targets `skip_reason='capped'`. Task 8: `test_402_treated_as_cap`.
4. **The cap is hit in the middle of a quadtree.** Expected: no more requests are sent, rows already fetched are kept, and tiles left unqueried are counted in `notes` (`unqueried_tiles=N`). Task 4: `test_cap_mid_tree_stops_cleanly`.
5. **Firecrawl returns `success:true` with `metadata.statusCode` 404/500.** Expected: the row is landed with `http_status` = the page status, `markdown` as given, and `skip_reason=NULL`. It is not an error row and is not retried. Task 7: `test_target_404_lands_with_status`.

---

## File Structure

```
config/categories.yaml        area, categories, crawl config (social domains, UA, legal regex)
.env.example                  every env var, empty values
DECISIONS.md                  D1–D9
README.md                     how to run, cost per run
src/__init__.py               (empty; makes src a regular package)
src/settings.py               Settings (env) + AppConfig (yaml); __main__ prints masked config
src/run.py                    RunContext, new_run()
src/log.py                    log(event, **fields) JSON-lines
src/db.py                     connect(), ensure_schema(), insert_rows(), step()
src/cache.py                  DiskCache, TokenBucket, SourceClient, CapReached
src/extract/tiles.py          Tile, root_tiles(), split()
src/extract/places.py         search_tile(), run_places(); __main__ CLI
src/extract/crawl.py          normalise_url(), classify(), targets(), robots, scrape, select_legal_links(), run_crawl()
src/extract/sodra.py          download/resolve, map_headers(), load(), validate(), run_sodra()
src/extract/__main__.py       `python -m src.extract all|sodra|places|crawl|report`
src/extract/report.py         build_report(con, run_id) -> str
src/pipeline.py               main() calls extract all (dbt/export remain NotImplemented)
tests/conftest.py             tmp duckdb fixture, settings fixture, mock transport helper
tests/fixtures/places/*.json  recorded Text Search pages
tests/fixtures/firecrawl/*.json, robots/*.txt
tests/fixtures/sodra/monthly-sample.csv (20 rows) + built zip in test
```

---

### Task 1 (EXT-01): Project skeleton & settings

**Files:**
- Modify: `pyproject.toml`, `.gitignore`
- Create: `config/categories.yaml`, `.env.example`, `DECISIONS.md`, `src/__init__.py`, `src/settings.py`, `src/run.py`, `src/log.py`, `tests/conftest.py`, `tests/test_settings.py`, `tests/test_run.py`

**Interfaces:**
- Produces:
  - `class Settings(BaseSettings)`: `google_api_key: SecretStr`, `firecrawl_api_key: SecretStr`, `run_month: str | None`, `max_places_requests: int = 1000`, `max_firecrawl_pages: int = 600`, `places_rate_per_sec: float = 5`, `firecrawl_rate_per_min: float = 10`, `sodra_url: str | None`, `sodra_file: Path | None`, `db_path: Path = Path("data/leads.duckdb")`, `cache_dir: Path = Path("data/cache")`, `config_path: Path = Path("config/categories.yaml")`. Reads `.env`.
  - `class BBox(BaseModel)`: `south, west, north, east: float`. Validator: `-90≤south<north≤90`, `-180≤west<east≤180`.
  - `class Area`: `name: str`, `bbox: BBox`, `tile_km: float`, `min_tile_km: float = 0.5`. Validator: `tile_km ≥ min_tile_km`.
  - `class Category`: `places_included_type: list[str]` (min 1), `evrk_prefixes: list[str]`.
  - `class CrawlConfig`: `user_agent: str`, `social_domains: list[str]`, `legal_link_pattern: str`, `max_legal_links: int = 2`.
  - `class AppConfig`: `area: Area`, `categories: dict[str, Category]`, `crawl: CrawlConfig`.
  - `load_settings() -> Settings` and `load_config(path: Path) -> AppConfig`. Both raise `pydantic.ValidationError` on a bad value.
  - `@dataclass(frozen=True) class RunContext`: `run_id: str`, `run_month: str`, `started_at: datetime`.
  - `new_run(run_month: str | None = None, now: datetime | None = None) -> RunContext`.
  - `log(event: str, **fields) -> None`.

- [ ] **Step 1: Dependencies.** In `pyproject.toml`, add `httpx`, `duckdb`, `pyyaml`, `pydantic-settings`, `tenacity` to the dependencies and `pytest` as a dev dependency. Add `[tool.pytest.ini_options] pythonpath=["."]`, `markers=["live: hits real APIs"]`, `addopts="-m 'not live'"`. Run `uv sync`. `.gitignore` already has `data/*` and `.env`; leave `output/` tracked.
- [ ] **Step 2: `config/categories.yaml`.** Copy spec §2 with these changes: `tile_km: 8.0` (D1), `min_tile_km: 0.5`, and the EVRK prefixes from finding S2. Add:
  ```yaml
  crawl:
    user_agent: "LeadsResearchBot/0.1"
    social_domains: [facebook.com, fb.com, instagram.com, linktr.ee, wolt.com, bolt.eu,
                     tiktok.com, sites.google.com, business.site, g.page, youtube.com,
                     linkedin.com, booksy.com, fresha.com, treatwell.lt, manobooking.lt, page.link]
    legal_link_pattern: "kontakt|rekvizit|apie|about|contact|privatum|privacy|taisykl|terms|imone|company"
    max_legal_links: 2
  ```
- [ ] **Step 3: Write failing tests.**
  ```python
  # tests/test_run.py
  def test_run_id_format():
      r = new_run("2026-10", now=datetime(2026,10,19,14,15,2,tzinfo=UTC))
      assert r.run_id == "2026-10_20261019T141502" and r.run_month == "2026-10"
  def test_run_month_defaults_to_current_utc_month():
      assert new_run(None, now=datetime(2026,1,31,23,0,tzinfo=UTC)).run_month == "2026-01"
  def test_bad_run_month_rejected():   # "2026-13", "202610", "26-10"
      with pytest.raises(ValueError): new_run("2026-13")
  # tests/test_settings.py
  def test_inverted_bbox_fails(tmp_path): ...  # south>north -> ValidationError
  def test_missing_google_key_fails(monkeypatch): ...  # delenv -> ValidationError
  def test_config_loads_repo_yaml(): assert set(load_config(Path("config/categories.yaml")).categories) == {"cafes_restaurants","hair_beauty","car_service"}
  def test_masked_print_hides_keys(capsys, monkeypatch): ...  # key "AIzaSECRET123" -> output contains "AIza…", not "SECRET"
  ```
- [ ] **Step 4: Run.** `uv run pytest tests/test_run.py tests/test_settings.py -v` should FAIL with an ImportError.
- [ ] **Step 5: Implement** `src/settings.py`, `src/run.py` and `src/log.py` to the interfaces above. `run_month` is validated with `^\d{4}-(0[1-9]|1[0-2])$`. `python -m src.settings` prints `{"settings": ..., "config": ...}` as JSON, with each `SecretStr` shown as its first 4 characters + `…`. `log` writes `json.dumps({"ts": utc iso, "event": ..., **fields}, default=str)` to stdout.
- [ ] **Step 6: Write `.env.example`** with every `Settings` field in upper case and no values. Write `DECISIONS.md` with D1–D9 from this plan.
- [ ] **Step 7: Verify.** Run `uv run pytest -v`, which should PASS. Then run `uv run python -m src.settings`: it should print the config with masked keys and exit 0. With `GOOGLE_API_KEY` unset it should exit non-zero with a validation error.
- [ ] **Step 8: Commit.** `EXT-01: project skeleton, typed settings and run identity`

### Task 2 (EXT-02): DuckDB raw schema & writer

**Files:** Create `src/db.py`, `tests/test_db.py`

**Interfaces:**
- Consumes: `RunContext`, `log`.
- Produces:
  - `connect(path: Path) -> duckdb.DuckDBPyConnection` (creates the parent directory, then calls `ensure_schema`).
  - `ensure_schema(con) -> None`: `CREATE SCHEMA IF NOT EXISTS raw` and `CREATE TABLE IF NOT EXISTS` for each table in spec §1. `payload` and `links` are `JSON`; all `raw.sodra` data columns are `VARCHAR` and keep the camelCase names quoted (`"jarCode"`).
  - `insert_rows(con, table: str, rows: list[dict], run: RunContext, retrieved_at: datetime | None = None) -> int`: fills in `run_id` and `retrieved_at` (default: now, UTC) unless the row already has them, and uses `executemany` with the column list taken from the first row. JSON columns are passed as `json.dumps` strings. Returns the number of rows inserted.
  - `@dataclass class StepStats`: `items_ok=0, items_failed=0, api_requests=0, cache_hits=0, status: str | None = None, notes: list[str]`.
  - `@contextmanager step(con, run: RunContext, name: str) -> Iterator[StepStats]`: on exit it writes one `raw.run_log` row. Status precedence: an exception → `failed` (the note gets the exception class and message, then the exception is re-raised); `stats.status` if set (e.g. `capped`); else `partial` if `items_failed > 0`; else `ok`. Notes are joined with `"; "`. It also logs a `step_finished` event.

- [ ] **Step 1: Failing tests.** In a temporary DuckDB:
  - `test_ensure_schema_idempotent`: call it twice, and the table list stays the same.
  - `test_roundtrip_each_table`: insert one row into each of the 6 tables, read it back, and check that `run_id` was filled in and that `payload->>'id'` works.
  - `test_step_writes_failed_on_exception`: `with pytest.raises(RuntimeError): with step(...): raise RuntimeError("x")` → one `run_log` row with `status='failed'` and notes containing `"RuntimeError: x"`.
  - `test_step_partial_and_capped`: `items_failed=1` → `partial`; `stats.status="capped"` → `capped`.
  - `test_sodra_leading_zero_kept`: insert `code="0012"` and read back `"0012"`.
- [ ] **Step 2: Run** `uv run pytest tests/test_db.py -v` → FAIL.
- [ ] **Step 3: Implement** `src/db.py`.
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit.** `EXT-02: DuckDB raw schema, batch insert and run_log step context`

### Task 3 (EXT-03): HTTP cache, rate limiter, retries

**Files:** Modify `src/cache.py`. Create `tests/test_cache.py`.

**Interfaces:**
- Produces:
  - `request_hash(method: str, url: str, body: dict | None) -> str`: sha256 hex of `f"{METHOD}\n{url}\n{json.dumps(body, sort_keys=True, separators=(',',':'))}"`. Headers are **never** part of the hash, so keys stay out of it.
  - `class DiskCache(root: Path, ttl: timedelta | None)`: `get(key) -> CachedResponse | None` and `put(key, status, headers: dict, body: bytes, fetched_at)`. Files are stored at `{root}/{key[:2]}/{key}.json`, with the body base64-encoded. With `ttl=None`, entries never expire. Only 2xx responses are stored.
  - `@dataclass CachedResponse`: `status: int, headers: dict, content: bytes, fetched_at: datetime, from_cache: bool`, plus a `.json()` method.
  - `class TokenBucket(rate_per_sec: float, burst: int = 1, clock=time.monotonic, sleep=time.sleep)`: `acquire()`.
  - `class CapReached(Exception)`.
  - `class AuthError(Exception)` is raised on 401/403. `class ApiError(Exception)` has `.status: int | None` and `.message: str`, and is raised after retries run out or on another 4xx.
  - `class SourceClient(source: str, cache: DiskCache | None, limiter: TokenBucket, max_requests: int | None, headers: dict, transport: httpx.BaseTransport | None = None, timeout: float = 30)`:
    - `request(method, url, json_body=None, use_cache=True) -> CachedResponse`.
    - Counters `api_requests` and `cache_hits`.
    - Before each network attempt (retries included), `api_requests >= max_requests` raises `CapReached`.
    - Retries go through tenacity, `stop_after_attempt(3)` + `wait_exponential_jitter(initial=1, max=20)`, on `httpx.TimeoutException`, `httpx.TransportError`, 429 and 5xx. Honour `Retry-After` on 429 when it is ≤ 60 s.
    - Factories `places_client(settings)`, `firecrawl_client(settings)`, `robots_client(settings)` and `sodra_client(settings)` set the TTLs from D8, the rates from Settings, and the headers (`X-Goog-Api-Key`; `Authorization: Bearer`; the declared User-Agent for robots and Sodra).

- [ ] **Step 1: Failing tests** with an `httpx.MockTransport` that counts calls:
  - `test_second_identical_request_hits_cache`: 2 calls → transport called once, `api_requests==1`, `cache_hits==1`, `from_cache` True on the second.
  - `test_different_body_misses_cache`.
  - `test_api_key_not_in_hash_or_cache_files`: walk the cache directory and check that no file contains the key string.
  - `test_ttl_expiry`: put with `fetched_at` 31 days ago and TTL 30 days → miss.
  - `test_retries_429_then_success`: responses 429, 200 → 2 calls, success. Inject a no-op sleep.
  - `test_400_not_retried`: one call, then `ApiError(status=400)`.
  - `test_401_raises_auth_error`.
  - `test_cap_raises_before_send`: with `max_requests=1`, the second distinct request raises `CapReached` and the transport is called once.
  - `test_token_bucket_spacing`: with a fake clock at 5 rps, 3 acquires sleep a total of ≈0.4 s.
- [ ] **Step 2: Run** `uv run pytest tests/test_cache.py -v` → FAIL.
- [ ] **Step 3: Implement** `src/cache.py`.
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit.** `EXT-03: disk cache, token bucket, retries and request counters`

### Task 4 (EXT-04): Places tiling & search

**Files:** Create `src/extract/tiles.py`, `tests/test_tiles.py`, `tests/test_places.py`, `tests/fixtures/places/`. Modify `src/extract/places.py`.

**Interfaces:**
- Consumes: `SourceClient`, `CapReached`, `AuthError`, `ApiError`, `request_hash`, `insert_rows`, `step`, `StepStats`, `AppConfig`.
- Produces:
  - `@dataclass(frozen=True) class Tile`: `tile_id: str, south, west, north, east: float, size_km: float`.
  - `root_tiles(bbox: BBox, tile_km: float) -> list[Tile]`: `rows = ceil(lat_km / tile_km)` and `cols = ceil(lon_km / tile_km)`, with `lat_km = Δlat×110.574` and `lon_km = Δlon×111.320×cos(mid_lat)`. Cells are equal fractions of the bbox (so nothing spills over the edge), with ids `r{row}c{col}` in row-major order starting from the south-west.
  - `split(t: Tile) -> list[Tile]`: 4 children with ids `{id}.0`–`{id}.3` (SW, SE, NW, NE) and `size_km/2`.
  - `FIELD_MASK: str` is the spec's list joined with `,`. `SEARCH_URL = "https://places.googleapis.com/v1/places:searchText"`.
  - `build_body(included_type: str, tile: Tile, page_token: str | None) -> dict` → `{"textQuery": type.replace("_"," "), "includedType": type, "strictTypeFiltering": True, "pageSize": 20, "languageCode": "lt", "regionCode": "LT", "locationRestriction": {"rectangle": {"low": {...}, "high": {...}}}, "pageToken"?: ...}`.
  - `@dataclass TileResult`: `places: list[tuple[str, dict, str, datetime]]` (place_id, place object, request_hash, retrieved_at), `saturated: bool`, `error: tuple[int|None, str, str] | None` (http_status, error, request_hash).
  - `search_tile(client, included_type, tile) -> TileResult`: pages through up to 3 pages. `saturated = (total == 60)`. On `ApiError` for a page after the first with status 400, it re-runs the whole chain once with `use_cache=False`. `AuthError` and `CapReached` propagate.
  - `run_places(con, client, config: AppConfig, run: RunContext, categories: list[str] | None = None) -> StepStats`. It runs inside `step(con, run, "places")`.

  Quadtree per (category, type). The order is deterministic: categories sorted, types in config order, tiles in a FIFO queue.
  ```
  queue = root_tiles(...)
  while queue:
      t = queue.pop(0); r = search_tile(client, type, t)
      collect r.places / r.error
      if r.saturated:
          if t.size_km / 2 >= min_tile_km: queue += split(t)
          else: gaps.append((category, type, t.tile_id))   # log event "coverage_gap"
  ```
  - Within a category, keep the first occurrence of each `place_id` and insert after the category finishes. Rows: `category, tile_id, request_hash, place_id, payload, retrieved_at`.
  - Errors are written to `raw.places_errors`, and `items_failed += 1` for each.
  - `items_ok` = unique rows inserted.
  - Saturated min-size tiles go into notes as `coverage_gaps=N`. A saturated tile always shows up as a `tile_saturated` log event, so the report can read saturation from `run_log.notes` (see Task 12).
  - On `CapReached`: insert what has been collected, set `stats.status="capped"`, and add the note `unqueried_tiles=<len(queue)+remaining types/categories roots>`.
  - On `AuthError`: re-raise so the step becomes `failed`.
  - Copy the counters from the client into the stats at the end.
- [ ] **Step 1: Fixtures.** Recorded or hand-built JSON: `page_full_{1,2,3}.json` (20 places each, the first two pages carrying a `nextPageToken`), `page_partial.json` (7 places, no token), and `dupes.json` (with one `id` that also appears in `page_partial`). Use the real response shape `{"places":[{"id":..,"displayName":{"text":..}, "websiteUri":..}], "nextPageToken":..}`. A routing MockTransport picks the fixture by `(tile_id derived from rectangle, pageToken)`.
- [ ] **Step 2: Failing tests.**
  - `test_root_tiles_cover_bbox`: Vilnius bbox at 8 km → 16 tiles; the union of their edges equals the bbox; ids run `r0c0..r3c3`.
  - `test_split_quadrants`: the children's bounds touch, `size_km` is halved, ids end `.0`–`.3`.
  - `test_build_body`: has `textQuery == "hair salon"` and `strictTypeFiltering is True`, has no `pageToken` on the first page, and the field mask header equals `FIELD_MASK`.
  - `test_pagination_collects_three_pages`: 60 places and `saturated` True. With a partial page: 7 places, `saturated` False.
  - `test_saturation_splits_until_min`: the root returns 60, so 4 children are queried. One child at the minimum size still returns 60 → `coverage_gaps=1` appears in the notes, and the 0.5 km tile is not split.
  - `test_dedupe_within_category`: the same id from two tiles → one row in `raw.places`. The same id in two categories → two rows.
  - `test_error_row_and_continue`: one tile returns 500 three times → one `places_errors` row with `http_status=500`, other tiles still land, status `partial`.
  - `test_auth_error_fails_step`: 403 → `run_log.status='failed'`, and exactly 1 transport call.
  - `test_cap_mid_tree_stops_cleanly`: `max_requests=5` → `status='capped'`, `api_requests==5`, the rows fetched so far are present, and the notes contain `unqueried_tiles=`.
  - `test_stale_page_token_refetches_chain`: page 1 is cached; page 2 returns 400 the first time → the chain is fetched again without the cache, and 60 places result.
  - `test_run_log_counts`: `api_requests` in `run_log` equals the number of transport calls.
  - `@pytest.mark.live test_live_one_tile_one_type`: one 1 km tile in the centre of Vilnius, type `cafe` → at least 1 row.
- [ ] **Step 3: Run** `uv run pytest tests/test_tiles.py tests/test_places.py -v` → FAIL.
- [ ] **Step 4: Implement** `tiles.py` and the functions in `places.py`.
- [ ] **Step 5: Run** → PASS. Then `LIVE=1 uv run pytest -m live tests/test_places.py -v` → PASS. This costs 1–3 requests, within the free tier.
- [ ] **Step 6: Commit.** `EXT-04: Places quadtree tiling, paginated search, saturation split, dedupe`

### Task 5 (EXT-05): Places CLI across categories

**Files:** Modify `src/extract/places.py` (add `main()` and `if __name__`), `README.md`. Create `tests/test_places_cli.py`.

**Interfaces:**
- Consumes: `run_places`, `connect`, `new_run`, `places_client`.
- Produces: `places_summary(con, run_id) -> list[dict]` with keys `category, places, unique_places, with_website, saturated_tiles`. `places` = rows. `unique_places` = distinct `place_id`. `with_website` = rows where `payload->>'websiteUri'` is not null. `saturated_tiles` comes from `run_log.notes` `coverage_gaps` for the category, so add the per-category notes `"{category}:coverage_gaps=N"`. `main(argv)` takes `--category` (repeatable) and `--run-id` (to reuse an id). It prints the summary as a JSON line per category and exits 1 on `failed`.

- [ ] **Step 1: Failing test** `test_summary_matches_sql`: run with fixtures across 2 categories, then assert that every summary number equals the matching `SELECT count(*)` / `count(DISTINCT place_id)` / `count(*) FILTER (WHERE payload->>'websiteUri' IS NOT NULL)`.
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4: Run** → PASS.
- [ ] **Step 5: Live full run.** `uv run python -m src.extract.places` → `run_log.status` is `ok` or `partial`, not `capped`, with `api_requests ≤ 1000`. If it comes back `capped`, stop and report the numbers to the user before raising the cap (that spends money).
- [ ] **Step 6: README.** Add a "Cost per run" section with the measured request count, the Enterprise SKU, the free 1,000 events per month, and $35 per 1,000 above that.
- [ ] **Step 7: Commit.** `EXT-05: Places CLI over all categories with summary`

### Task 6 (EXT-06): Crawl target selection

**Files:** Modify `src/extract/crawl.py`. Create `tests/test_crawl_targets.py`.

**Interfaces:**
- Produces:
  - `normalise_url(url: str) -> str`: lowercase the scheme and host; drop the default port and the fragment; drop query keys `utm_*`, `fbclid`, `gclid`, `yclid`, `mc_cid`, `mc_eid`, `ref`, `_ga`; keep the other query keys sorted; an empty path becomes `/`. Raises `ValueError` if the URL has no host.
  - `dedupe_key(norm_url: str) -> str`: the host without a leading `www.`, plus the path and query. The scheme is ignored, so `http://www.x.lt` and `https://x.lt/` collapse to one key.
  - `classify(website: str | None, social_domains: list[str]) -> Literal["no_website","social_domain","crawl"]`: matches the host exactly or by suffix with `"." + domain`. An unparseable URL counts as `no_website`.
  - `@dataclass Target`: `url: str` (normalised; the first in sorted order among those sharing a key), `place_ids: list[str]` (sorted), `cls: str`.
  - `targets(con, run_id: str, crawl_cfg: CrawlConfig) -> list[Target]`: reads `DISTINCT place_id, payload->>'websiteUri'` from `raw.places` for the run. Places with `no_website` or `social_domain` each get their own `Target` with `url` = the raw value or `""`. Places to `crawl` are grouped by `dedupe_key`. The result is sorted by `(cls, url)`.
- [ ] **Step 1: Failing tests.** `test_classify_samples` is parametrised over 15 cases:

  | input | class |
  |---|---|
  | `None`, `""`, `"not a url"` | no_website |
  | `https://www.facebook.com/kavine`, `https://m.facebook.com/x`, `https://instagram.com/x`, `https://linktr.ee/x`, `https://wolt.com/lt/ltu/vilnius/restaurant/x`, `https://food.bolt.eu/x`, `https://sites.google.com/view/x`, `https://booksy.com/lt-lt/x` | social_domain |
  | `https://Kavine.LT/?utm_source=google#top`, `http://www.chain.lt`, `https://chain.lt/`, `https://notfacebook.com` | crawl |

  Also: `test_normalise_strips_tracking`, where `https://Kavine.LT/?utm_source=g&b=2&a=1#top` → `https://kavine.lt/?a=1&b=2`. And `test_chain_dedupe`: three places with `http://www.chain.lt`, `https://chain.lt/` and `https://chain.lt/?utm_medium=x` → one Target with 3 `place_ids`.
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4: Run** → PASS.
- [ ] **Step 5: Commit.** `EXT-06: crawl target normalisation, classification and chain dedupe`

### Task 7 (EXT-07): Robots & homepage scrape

**Files:** Modify `src/extract/crawl.py`. Create `tests/test_crawl_scrape.py`, `tests/fixtures/firecrawl/{home_ok.json,home_404.json}`, `tests/fixtures/robots/{allow.txt,disallow_all.txt}`.

**Interfaces:**
- Consumes: `Target`, `targets`, `SourceClient` (firecrawl plus robots), `insert_rows`, `step`.
- Produces:
  - `RobotsChecker(client: SourceClient, user_agent: str)` with `allowed(url) -> tuple[bool, str | None]`. It keeps one parser per scheme and host in memory and loads `{scheme}://{host}/robots.txt` through the cached client. Outcomes follow D9: 2xx is parsed; 4xx allows everything; 5xx or a network error gives `(False, "robots_unreachable")`.
  - `FIRECRAWL_URL = "https://api.firecrawl.dev/v2/scrape"`.
  - `scrape(client, url: str) -> PageResult` sends `{"url": url, "formats": ["markdown","links"], "onlyMainContent": False}`.
  - `@dataclass PageResult`: `final_url, http_status: int | None, markdown: str | None, links: list[str] | None, error: str | None, retrieved_at: datetime`. `final_url` comes from `data.metadata.url` and `http_status` from `data.metadata.statusCode`.
  - `page_rows(target: Target, role: str, requested_url: str, result: PageResult | None, skip_reason: str | None) -> list[dict]` produces one dict per `place_id`. `content_sha256` is the sha256 of the markdown, or `None`. `links` is set only for `home`.
  - `run_crawl(con, fc_client, robots_client, config, run, max_pages: int) -> StepStats` runs inside `step(con, run, "crawl")`. For each target:
    - `no_website` / `social_domain` → a skip row.
    - Robots disallow → `robots_disallow`.
    - Otherwise scrape. `ApiError`, or `success: false` → `skip_reason='error'`, `items_failed += 1`.
    - `AuthError` → the step fails.
    - Target pages that come back 404/500 are landed as normal rows (Review Focus 5).
    - `items_ok` counts pages fetched, not place rows.
- [ ] **Step 1: Failing tests.**
  - `test_robots_allow_then_scrape`: the row has markdown, `final_url`, `http_status=200` and a `content_sha256` of 64 hex characters.
  - `test_robots_disallow_skips_without_firecrawl_call`: the Firecrawl transport is never called.
  - `test_robots_404_allows`.
  - `test_robots_500_disallows`.
  - `test_firecrawl_error_row`: Firecrawl returns 500 three times → `skip_reason='error'`, status `partial`.
  - `test_target_404_lands_with_status`.
  - `test_auth_error_fails_step`.
  - `test_chain_lands_row_per_place`: one Target with 3 place_ids → 3 rows and 1 Firecrawl call.
  - `test_no_website_row_written`: the denominator includes it.
  - `@pytest.mark.live test_live_five_sites`: 5 known Vilnius cafés taken from the live places run. Assert markdown is non-empty, then spot-check by hand that each footer contains "UAB" / "įmonės kodas" / the address.
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4: Run** → PASS. Then run live (5 credits).
- [ ] **Step 5: Commit.** `EXT-07: robots check and Firecrawl homepage scrape`

### Task 8 (EXT-08): Legal-detail candidate pages & page cap

**Files:** Modify `src/extract/crawl.py`. Create `tests/test_crawl_legal.py`.

**Interfaces:**
- Produces:
  - `markdown_anchors(markdown: str) -> dict[str, str]`: maps each absolute URL to its anchor text, parsed with `\[([^\]]*)\]\((\S+?)(?:\s+"[^"]*")?\)`. Relative URLs are resolved against the page's `final_url`. The first anchor seen for a URL wins.
  - `select_legal_links(home_url: str, links: list[str], anchors: dict[str,str], pattern: str, k: int = 2) -> list[str]`. Steps:
    - Keep links whose host matches `home_url`'s once a leading `www.` is stripped, and whose scheme is http(s).
    - Normalise each with `normalise_url` and drop the homepage itself.
    - Match `pattern` (case-insensitive) against the URL path plus the anchor text.
    - Rank: tier 0 if `rekvizit` matches, tier 1 if `kontakt`, else tier 2. Within a tier, keep first-seen link order.
    - Dedupe and take the first k.
  - In `run_crawl`: after a successful home scrape, scrape the selected links with `page_role='legal_candidate'` (robots-checked, `links=None`).
    - A shared counter `pages_sent` covers both roles. Before each Firecrawl call, `pages_sent >= max_pages` → status `capped`, the note `firecrawl_pages_cap=<max_pages>`, and every remaining **home** target gets `skip_reason='capped'` rows. Legal candidates that were not fetched get no rows.
    - A 402 from Firecrawl is treated the same way, with the note `firecrawl_402` (Review Focus 3).
    - Cache hits do not count toward `pages_sent`.
- [ ] **Step 1: Failing tests.**
  - `test_select_prefers_rekvizit_then_kontakt`. Given links `[/apie, /kontaktai, /rekvizitai, /privatumo-politika, https://facebook.com/x, https://other.lt/kontaktai]` → `[/rekvizitai, /kontaktai]`.
  - `test_anchor_text_match`: `/p/12` with the anchor "Įmonės rekvizitai" → selected first.
  - `test_www_and_trailing_variants_same_domain`.
  - `test_homepage_and_fragment_links_excluded`: `/#kontaktai` normalises to the home URL and is dropped.
  - `test_no_matches_returns_empty`.
  - `test_cap_stops_cleanly`: 3 home targets, each with 2 legal links, and `max_pages=4` → exactly 4 Firecrawl calls, status `capped`, and the third target has a `capped` row.
  - `test_402_treated_as_cap`.
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4: Run** → PASS.
- [ ] **Step 5: Commit.** `EXT-08: legal-details candidate pages and Firecrawl page cap`

### Task 9 (EXT-09): Sodra download / resolve

**Files:** Modify `src/extract/sodra.py`. Create `tests/test_sodra_download.py`.

**Interfaces:**
- Produces:
  - `class SodraBlocked(Exception)`.
  - `acquire(settings, client: SourceClient | None, dest_dir: Path = Path("data/raw/sodra")) -> tuple[Path, str, str]` returns `(zip_path, source_url, sha256)`.
    - If `settings.sodra_file` is set, copy the file to `{dest_dir}/{sha}.zip`. `source_url` = `settings.sodra_url` or `file://{abs}`.
    - Otherwise, if `settings.sodra_url` is set, GET it with `use_cache=False` and stream the body to a temp file, then rename it to `{sha}.zip`.
    - A 403 whose body contains `"Just a moment"` or has the `cf-mitigated` header raises `SodraBlocked`, with a message telling the user to download the zip in a browser and set `SODRA_FILE`.
    - A response that does not start with the zip magic `PK\x03\x04` raises `ApiError`.
    - With neither setting → `ValueError("set SODRA_URL or SODRA_FILE")`.
  - `already_landed(con, sha: str) -> bool`: `SELECT 1 FROM raw.sodra WHERE file_sha256=? LIMIT 1`.
- [ ] **Step 1: Manual step for the user (D3).** Open `https://atvira.sodra.lt/imones/rinkiniai/index.html` in a browser and copy the href of the monthly CSV zip into `.env` as `SODRA_URL`. In `config/categories.yaml`, record it under `sodra.url_pattern`, with `{year}` in place of the year if the name follows `monthly-{year}.csv.zip`. Do not invent the URL. Until it exists, use `SODRA_FILE=data/raw/sodra/monthly-2026.csv.zip`.
- [ ] **Step 2: Failing tests.**
  - `test_local_file_copied_by_sha`.
  - `test_url_download_and_sha` (MockTransport serving a small zip).
  - `test_cloudflare_challenge_raises_blocked`.
  - `test_non_zip_body_rejected`.
  - `test_already_landed_true_after_insert`.
- [ ] **Step 3: Run** → FAIL. **Step 4: Implement.** **Step 5: Run** → PASS.
- [ ] **Step 6: Commit.** `EXT-09: Sodra acquisition by URL or local file with sha dedupe`

### Task 10 (EXT-10): Sodra parse, validate, land

**Files:** Modify `src/extract/sodra.py`. Create `tests/test_sodra_load.py`, `tests/fixtures/sodra/monthly-sample.csv`.

**Interfaces:**
- Consumes: `acquire`, `already_landed`, `step`.
- Produces:
  - `EXPECTED_COLUMNS = ["code","jarCode","name","municipality","ecoActCode","ecoActCodeStr","ecoActName","month","avgWage","numInsured","avgWage2","numInsured2","tax"]`.
  - `map_headers(headers: list[str]) -> list[str]`: takes the token with `\(([A-Za-z0-9]+)\)\s*$`, which also covers `registruota(municipality)` with no space. It raises `ValueError` naming any header without a token.
  - `sniff(sample: bytes) -> tuple[str, str]` returns `(encoding, delimiter)`. The encoding is `utf-8-sig` if there is a BOM, otherwise `utf-8`, falling back to `cp1257` on a decode error. The delimiter comes from `csv.Sniffer().sniff(first_line, delimiters=";\t,")`.
  - `@dataclass ValidationReport`: `rows: int`, `months: dict[str,int]`, `missing_columns: list[str]`, `jar9_share: float`, `bad_num_insured: int`, `failures: list[str]`, with `.ok` and `.to_text()`.
  - `validate(con, staging: str) -> ValidationReport` checks:
    - Every expected column is present.
    - The `month` values are `yyyymm`, contiguous, and start at `{year}01`.
    - Each month has ≥ `min_rows_per_month` rows (default 50,000, a parameter so tests can pass 10).
    - `jar9_share ≥ 0.99` over non-empty `jarCode`.
    - There are 0 non-empty `numInsured` values where `TRY_CAST(... AS BIGINT) IS NULL`.
  - `run_sodra(con, settings, client, run, min_rows_per_month=50_000) -> StepStats`. Steps:
    1. `acquire`. If `already_landed`, set the note `duplicate_sha=<sha[:12]>` and `items_ok=0`, return `ok`.
    2. Extract the single `.csv` member to a temp directory.
    3. Run `read_csv(path, delim=?, header=true, all_varchar=true, quote='"')` into a TEMP table, renamed with `map_headers`.
    4. `validate`, and print `report.to_text()`.
    5. If the report fails, raise `ValueError(failures)`, so the step is `failed` and nothing is inserted.
    6. `INSERT INTO raw.sodra SELECT ?, ?, ?, ?, month AS data_month, <cols> FROM tmp` in one statement inside a transaction.
    7. `items_ok` = rows. `api_requests` comes from the client.
  - The DuckDB `read_csv` produces an empty string `""` for quoted empty fields; keep that as is (raw). An empty unquoted field is NULL, which is also fine.
- [ ] **Step 1: Fixture.** `monthly-sample.csv` has 20 rows copied from the real file's header and the shape of its first rows (embassy rows with an empty `jarCode` and an empty `avgWage`), 10 for `202601` and 10 for `202602`, saved with a BOM and `;`. Include one `code` with a leading zero, e.g. `0012345`.
- [ ] **Step 2: Failing tests.**
  - `test_map_headers_real_header`, using the exact header line in finding Q4 → `EXPECTED_COLUMNS`.
  - `test_sniff_bom_semicolon`.
  - `test_load_fixture`: 20 rows land in `raw.sodra`, the leading zero is kept, the embassy row's `jarCode` is NULL or `''`, and `data_month` equals `month`.
  - `test_validation_fails_on_gap_month`: months 202601 and 202603 → failed, 0 rows inserted.
  - `test_validation_fails_on_bad_jar`: 2 of 10 jar codes are 8 digits.
  - `test_duplicate_sha_skips_insert`: a second run → rows unchanged, `run_log` row `ok` with a note `duplicate_sha=`.
  - `test_report_prints` (capsys).
- [ ] **Step 3: Run** → FAIL. **Step 4: Implement.** **Step 5: Run** → PASS.
- [ ] **Step 6: Real-file check.** `SODRA_FILE=data/raw/sodra/monthly-2026.csv.zip uv run python -c "from src.extract import sodra; sodra.main()"` (add a `main()` that sets up a run and connects) → the report shows 835,029 rows across 8 months and a status of `ok`.
- [ ] **Step 7: Commit.** `EXT-10: Sodra header mapping, validation and VARCHAR landing`

### Task 11 (EXT-11): Orchestrated entrypoint

**Files:** Create `src/extract/__main__.py`, `tests/test_extract_all.py`. Modify `src/pipeline.py`.

**Interfaces:**
- Consumes: `run_sodra`, `run_places`, `run_crawl`, `new_run`, `connect`, and the client factories.
- Produces:
  - `extract_all(settings, config, con, run: RunContext, clients: dict[str, SourceClient]) -> dict[str, str]`: runs `sodra → places → crawl` and returns a step → status map.
    - A step that raises is caught here after `step()` has recorded `failed`, and the next step still runs. The exception is crawl when places `failed`: crawl is then recorded as `failed` with the note `upstream_places_failed`, without making any calls.
  - `main(argv) -> int`. Subcommands: `all`, `sodra`, `places`, `crawl` (`--run-id`, defaulting to the latest places `run_id` for `RUN_MONTH`), and `report`. The exit code is 1 if any status is `failed`, else 0. `capped` and `partial` are logged as `warning` events.
  - `src/pipeline.py:main()` calls `extract.__main__.main(["all"])` and leaves dbt and export as `NotImplementedError` stubs behind a clear log line.
- [ ] **Step 1: Failing tests** (all fixtures, injected clients):
  - `test_all_writes_three_run_log_rows_same_run_id`.
  - `test_exit_nonzero_on_failed_step`: Sodra validation fails → exit 1, but places and crawl still ran.
  - `test_capped_is_exit_zero`.
  - `test_crawl_skipped_when_places_failed`.
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4: Run** → PASS.
- [ ] **Step 5: Live run.** `uv run python -m src.extract all` → `SELECT step, status FROM raw.run_log WHERE run_id=?` returns 3 rows. Places should mostly come from cache after Task 5.
- [ ] **Step 6: Commit.** `EXT-11: orchestrated extract entrypoint under one run_id`

### Task 12 (EXT-12): Extraction data-quality report

**Files:** Create `src/extract/report.py`, `tests/test_report.py`, and `output/{run_month}/extract_report.md` (committed).

**Interfaces:**
- Produces:
  - `build_report(con, run_id: str) -> str`: Markdown built only from SQL over `raw.*` for that `run_id`. Sections:
    - Places per category (rows, unique).
    - Website share = places with a `websiteUri` / all distinct places.
    - Crawled share = places with a `home` row where `skip_reason IS NULL` / all distinct places.
    - Skip-reason breakdown, from `GROUP BY skip_reason` over `page_role='home'`.
    - Saturated tiles and coverage gaps (parsed from `run_log.notes`).
    - Sodra rows per `data_month` and the share with a non-empty `jarCode`. The Sodra lookup uses the latest `file_sha256` landed at or before this run, because a duplicate-sha run lands no rows of its own.
    - `api_requests` and `cache_hits` per step.
    - Duration per step and in total, from `started_at`/`finished_at`.
  - `write_report(con, run_id, out_root=Path("output")) -> Path` writes `output/{run_month}/extract_report.md`. It is wired to `python -m src.extract report [--run-id]`.
- [ ] **Step 1: Failing tests.**
  - `test_report_from_db_only`: fill a temp DB by hand-inserting rows (no extractor run), build the report, and assert these strings are present: `"| cafes_restaurants | 3 | 3 |"`, `"no_website | 1"`, `"Sodra rows: 20"`, `"jarCode share: 90.0%"`.
  - `test_write_report_path`.
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4: Run** → PASS.
- [ ] **Step 5: Example run.** `uv run python -m src.extract report` for the Task 11 run → the file is written. Read it and check that its numbers match the summary from Task 5.
- [ ] **Step 6: Commit.** `EXT-12: extraction data-quality report` (including `output/2026-10/extract_report.md`)

---

## Live spend summary (needs your OK before Tasks 4, 5, 7, 11)

| Step | Spend |
|---|---|
| Task 4 live smoke | 1–3 Places requests |
| Task 5 full run | about 600–1,200 Places requests. Free up to 1,000/month on Enterprise, then $35 per 1,000 |
| Task 7 live | 5 Firecrawl credits |
| Task 11 full | up to 600 Firecrawl credits (free plan has 1,000/month; about 60 min at 10/min). Places is mostly served from cache |
