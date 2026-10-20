# Code Guide — Extraction Layer

How to read, verify and understand the `extract` branch. It takes about 2–3 hours if you do every hands-on check. Every command here runs **offline**. None of them touches Google, Firecrawl or Sodra.

Companion documents:
- `docs/specs/extraction-spec.md` is the requirements.
- `docs/superpowers/plans/2026-10-19-extraction-step.md` is the plan and its findings.
- `DECISIONS.md` records why the code departs from the spec in places (D1–D9, R6–R27).

---

## 0. The big picture (5 min)

```
                 config/categories.yaml + .env
                              │
                     src/settings.py  ──►  Settings (env), AppConfig (yaml)
                              │
           src/run.py ──► RunContext(run_id, run_month)     one per run
                              │
        ┌─────────────────────┼──────────────────────────┐
        ▼                     ▼                          ▼
 extract/sodra.py      extract/places.py           extract/crawl.py (+ robots.py)
 zip → validate →      quadtree tiles →            raw.places → targets →
 raw.sodra             Text Search pages →         robots check → Firecrawl
                       raw.places / places_errors  → raw.web_pages
        │                     │                          │
        └──── all HTTP goes through src/cache.py::SourceClient ────┘
              (disk cache · rate limit · retries · request cap · counters)
                              │
        every step runs inside src/db.py::step()  →  one raw.run_log row
                              │
              extract/__main__.py   `python -m src.extract all|sodra|places|crawl|report`
              extract/report.py     SQL-only data-quality report
```

There are three ideas to hold onto:
1. **Raw is raw.** Payloads are stored as-is (JSON or text) and append-only, and every row carries `run_id` and `retrieved_at`.
2. **One HTTP path.** Every network call goes through `SourceClient`, which handles caching, budgets, retries and secret hygiene in one place.
3. **One run_log row per step.** `step()` always writes it, even when the step crashes. `run_log` is the source of truth for status.

---

## 1. Setup check (5 min)

```bash
git switch extract
uv sync
uv run pytest -q                       # expect: 194 passed, 2 skipped (the 2 live tests)
uv run pytest -q -m recorded -v        # replay tests over your recorded real API calls
uv run python -m src.settings          # resolved config, keys masked as "AIza…"
```

When reading a module, run its test file with `-v` next to it. The test names describe the behaviour, e.g. `uv run pytest tests/test_places.py -v`.

---

## 2. Reading order

Read the modules in this order; each stop depends only on the ones before it.

### Stop 1: Settings, run identity, logging (15 min)
**Files:** `src/settings.py`, `src/run.py`, `src/log.py`, `config/categories.yaml`, `.env.example`

What to verify:
- `Settings` (`settings.py:11`) sets `hide_input_in_errors=True` (lines 12–13). Without it, a validation error would print other env values, including API keys, into the traceback.
- API keys are `SecretStr | None`. They are optional at load (R26) and only required when a client that needs them is built.
- `BBox._check` (`:36`) and `Area._check` (`:51`) are the fail-fast validation.
- `load_settings_or_exit` (`:93`) is what every CLI calls. It prints only the field names, then exits 2.
- `new_run` (`run.py:16`) builds `run_id = {YYYY-MM}_{UTC yyyymmddTHHMMSS}` and validates `RUN_MONTH`.
- `categories.yaml`:
  - `tile_km: 8.0` (D1) is the quadtree root size, not a fixed grid.
  - The EVRK prefixes are the 2025 codes (S2): `96.21/96.22`, `95.31`.
  - `page.link` is in `social_domains`.

Proving tests: `tests/test_settings.py` (`test_validation_error_hides_secret_values`, `test_cli_prints_only_field_names`, `test_masked_print_hides_keys`), and `tests/test_run.py`.

Hands-on:
```bash
GOOGLE_API_KEY=AIzaSECRETxyz MAX_PLACES_REQUESTS=notanumber uv run python -m src.settings; echo "exit=$?"
# expect "invalid setting: MAX_PLACES_REQUESTS", exit 2, and no "SECRET" in the output
```

### Stop 2: Database and the run_log contract (15 min)
**File:** `src/db.py`

What to verify:
- The DDL creates 5 tables in schema `raw` (R11). The Sodra columns are all `VARCHAR` with quoted camelCase names, so leading zeros survive.
- `insert_rows` (`:75`) stamps `run_id` and `retrieved_at` unless the row already has them. `_val` (`:69`) serialises dicts and lists to JSON and normalises tz-aware datetimes to naive UTC (R16).
- `step()` (`:116-154`) is the most important function in the codebase:
  - it captures `started_at` on entry;
  - in `finally` it decides the status: `failed` on an exception, otherwise the explicit `stats.status`, otherwise `partial` if `items_failed > 0`, otherwise `ok`;
  - it inserts exactly one `run_log` row and re-raises any exception.
- There is no `UPDATE` or `DELETE` anywhere (append-only, D2). Check with `git grep -niE "\bupdate\b|\bdelete\b" -- src/`.

Proving tests: `tests/test_db.py`, especially `test_step_writes_failed_on_exception`, `test_step_ok_partial_and_capped` and `test_sodra_leading_zero_kept`.

### Stop 3: The HTTP layer (30 min, the core)
**File:** `src/cache.py`

Read `SourceClient.request` (`:193`) and `_attempt` (`:225`) together. This is the sequence:

```
request(): key = request_hash(method, url, body)   ← headers (API keys!) never in the key
           cache hit?  → cache_hits += 1, return            (no limiter, no cap, no network)
           tenacity Retrying(3 attempts, exp. backoff + jitter, honours Retry-After ≤ 60 s)
             _attempt(): cap check (api_requests >= max_requests → CapReached)  ← before EVERY attempt
                         limiter.acquire(); api_requests += 1; send
                         2xx → return | 401/403 → AuthError | 429/5xx → retry | other 4xx → ApiError
           store 2xx in cache only if cacheable(resp)   ← Firecrawl success:false is not cached (R27)
```

What to verify:
- `request_hash` (`:59`) is `sha256("METHOD\nurl\ncanonical-json-body")`. The recorded fixtures are named by this hash. `test_request_hash_is_stable` pins its exact value.
- Cache files (`DiskCache.put`, `:102`) hold the response status, response headers and body, never request headers, and are written atomically (tmp file, then rename).
- The factories (`:261-298`) set the budgets and policies:

  | factory | cache TTL | rate | cap | redirects | timeout |
  |---|---|---|---|---|---|
  | `places_client` | 30 d | 5/s | `MAX_PLACES_REQUESTS` | no | 30 s |
  | `firecrawl_client` | 30 d | 10/min | set per run by `run_crawl` | no | 90 s |
  | `robots_client` | 1 d | places rate | none | yes | 30 s |
  | `sodra_client` | none | 1/s | none | yes | 30 s |

Proving tests: all of `tests/test_cache.py`. The key ones are `test_api_key_not_in_hash_or_cache_files`, `test_cap_counts_retries`, `test_cap_raises_before_send`, `test_non_2xx_not_cached` and `test_per_request_headers_merged_and_not_hashed`.

Hands-on: open any file in `tests/fixtures/recorded/places/`. It contains `request` (method, url, body) and `response`, and no headers. Then:
```bash
grep -rl "$(grep '^GOOGLE_API_KEY=' .env | cut -d= -f2)" tests/fixtures data/cache 2>/dev/null || echo "key not stored anywhere"
```

### Stop 4: Sodra, the offline one, with real data (20 min)
**File:** `src/extract/sodra.py`

The flow, in `run_sodra` (`:243`):
1. `acquire` (`:50`):
   - `SODRA_FILE` takes precedence: the file is copied, never moved.
   - Otherwise `SODRA_URL` is downloaded. The site is behind Cloudflare, and `_is_challenge` (`:32`) turns that 403 into a clear "download it in a browser" error.
   - Both paths check the zip magic bytes and store the file as `data/raw/sodra/{sha}.zip`.
2. `already_landed` (`:89`): if this sha was landed before, insert nothing and record status `ok` with the note `duplicate_sha=`.
3. `sniff` (`:125`) detects the encoding and delimiter:
   - The encoding is `utf-8-sig` when a BOM is present.
   - The delimiter is the most frequent of `; \t ,` in the header line. `csv.Sniffer` alone picks `,`, because one header contains "Savivaldybė, kurioje".
4. `map_headers` (`:101`) keeps the English token in parentheses: `Draudėjo kodas (code)` becomes `code`.
5. `_load_staging` (`:221`) runs DuckDB `read_csv(all_varchar=true)` into a temp table.
6. `validate` (`:164`) runs before any insert. Under R9 the months must be contiguous starting at `YYYY01`, with at least 50k rows per month. It also requires a jarCode 9-digit share of at least 99% and an integer `numInsured`.
7. A single `INSERT … SELECT` runs inside a transaction, so either everything lands or nothing does.

Proving tests: `tests/test_sodra_download.py` and `tests/test_sodra_load.py` (`test_validation_fails_on_gap_month`, `test_duplicate_sha_skips_insert`, `test_map_headers_real_header`).

Hands-on, against your real database (read-only):
```bash
uv run python - <<'EOF'
import duckdb
c = duckdb.connect("data/leads.duckdb", read_only=True)
print(c.sql("select run_id, step, status, items_ok, notes from raw.run_log order by finished_at"))
print(c.sql("select data_month, count(*) n from raw.sodra group by 1 order by 1"))
print(c.sql("""select "ecoActCodeStr", count(*) from raw.sodra
               where data_month='202608' and ("ecoActCodeStr" like '56.1%' or "ecoActCodeStr" like '96.2%'
               or "ecoActCodeStr" like '95.31%') group by 1 order by 1"""))
EOF
```
Expect 8 months of about 105k rows each (840,194 in total). The last query shows that the corrected EVRK prefixes match real codes.

### Stop 5: Places, the quadtree (30 min)
**Files:** `src/extract/tiles.py`, `src/extract/places.py`

Read in this order:
1. `tiles.py`:
   - `root_tiles` (`:26`) splits the bbox into `ceil(km / tile_km)` cells per axis; for Vilnius at 8 km that is 4×4 = 16.
   - `_edges` (`:21`) pins the outer edges exactly to the bbox, so there are no float gaps.
   - `split` (`:41`) produces the 4 quadrants `.0`–`.3`.
2. `build_body` (`:30`): `textQuery` is required by the API (finding Q1), so it is the type name. It also sets `includedType`, `strictTypeFiltering`, `pageSize` 20, `lt`/`LT` and the `locationRestriction.rectangle`.
3. `_fetch_chain` (`:69`) fetches up to 3 pages following `nextPageToken`. It sends the field mask as a per-request header, which is kept out of the cache key.
4. `search_tile` (`:91`):
   - **Saturated** means 60 results (`MAX_RESULTS`, `:27`).
   - If a later page returns 400 (a stale token behind a cached page 1), the whole chain is fetched again once, bypassing the cache.
5. `_run_work` (`:127-188`) runs the quadtree loop, one FIFO queue per (category, type):
   - A saturated tile is split if `size/2 >= min_tile_km`. Otherwise it is counted as a **coverage gap**.
   - Places are deduplicated by `place_id` within a category only. The same place in two categories keeps both rows.
   - A cap hit partway through a chain still lands the pages already fetched (`CapWithPartial`, R17), and the note `unqueried_tiles=N` counts what was left.
   - The `finally` inserts collected rows even on `AuthError`.
   - The notes `{category}:saturated_tiles=N` and `{category}:coverage_gaps=N` are what the summary and the report parse (R5).

Proving tests: `tests/test_tiles.py`, `tests/test_places.py` (`test_saturation_splits_until_min`, `test_stale_page_token_refetches_chain`, `test_cap_mid_chain_lands_fetched_pages`, `test_dedupe_within_category_not_across`), `tests/test_places_cli.py`, and `test_recorded_replay_search_and_run`. The last one replays your real Old Town cafe (45 results) and restaurant (60, saturated) calls.

Hands-on:
```bash
uv run python -c "
from src.settings import load_config; from src.extract.tiles import root_tiles
c=load_config('config/categories.yaml'); t=root_tiles(c.area.bbox, c.area.tile_km)
print(len(t), t[0])"
```

### Stop 6: Crawl (40 min)
**Files:** `src/extract/crawl.py`, `src/extract/robots.py`

Read in this order:
1. `normalise_url` (`:46`) lowercases the host, drops the default port, the fragment and tracking keys (`utm_*`, `fbclid` …), sorts the query, and turns an empty path into `/`. `dedupe_key` (`:63`) ignores the scheme and a leading `www.`, so chain sites collapse into one key.
2. `classify` (`:74`) returns `no_website`, `social_domain` or `crawl`. Unparseable URLs and bad ports count as `no_website` (R19).
3. `targets` (`:101`):
   - One `Target` per site, holding every `place_id` that shares it.
   - The representative URL is chosen by `_prefer` (`:89`): https first, then no `www.`, then the shortest (R18).
4. `robots.py`:
   - `RobotsChecker` decodes bodies as `utf-8-sig`, so a BOM can't hide `Disallow`.
   - 4xx means allow all. 5xx, network failures and 429 mean disallow (D9, R20).
   - Parsers are cached per host.
5. `scrape` (`:141`) sends a Firecrawl v2 request with `markdown + links` and `onlyMainContent: false` (footers carry the legal details).
   - Firecrawl's HTTP status and the page's `metadata.statusCode` are separate. A 404 page with content is still landed.
   - A Firecrawl 403 is a per-URL error (R23); a 401 fails the step.
6. `select_legal_links` (`:197`) picks up to 2 same-domain links matching the legal pattern, by path or by markdown anchor text (`markdown_anchors`, `:188`). The rank order is `rekvizit`, then `kontakt|contact` (R21), then the rest.
7. `run_crawl` (`:228-329`) works depth-first: each site's homepage, then its legal pages.
   - The page budget counts Firecrawl attempts, retries included (R24).
   - It sets `fc_client.max_requests` for the run and restores it in `finally`.
   - A 402 (out of credits) behaves like the cap.
   - Every listing gets a row. No-website, social, robots-disallowed and capped listings carry a `skip_reason`, which keeps the coverage denominator honest.
   - `api_requests` counts Firecrawl only. Robots counts go into the notes (R25).

Proving tests:
- `tests/test_crawl_targets.py` (`test_classify_samples` is the 15-URL table, plus `test_chain_dedupe`)
- `tests/test_crawl_scrape.py` (`test_robots_bom_disallow_all`, `test_firecrawl_403_is_item_error_and_next_site_scraped`, `test_cap_cannot_be_exceeded_by_retries_within_a_scrape`, `test_target_404_lands_with_status`)
- `tests/test_crawl_legal.py`
- the recorded tests `test_recorded_homepages` and `test_recorded_homepages_legal_picks`

Hands-on: see which legal pages it would pick on your real recorded homepages:
```bash
uv run python - <<'EOF'
import json, glob
from src.extract.crawl import markdown_anchors, select_legal_links
from src.settings import load_config
cfg = load_config("config/categories.yaml").crawl
for f in sorted(glob.glob("tests/fixtures/recorded/firecrawl/*.json")):
    d = json.load(open(f))["response"]["json"]["data"]; home = d["metadata"]["url"]
    print(home, select_legal_links(home, d["links"], markdown_anchors(d["markdown"], home),
                                   cfg.legal_link_pattern, cfg.max_legal_links))
EOF
```

### Stop 7: Orchestration and report (20 min)
**Files:** `src/extract/__main__.py`, `src/extract/report.py`, `src/pipeline.py`

What to verify:
- `extract_all` (`__main__.py:69`) runs sodra, places and crawl under **one** `RunContext`.
  - Each step is wrapped by `_guarded` (`:29`), so one crash doesn't stop the next step.
  - If places failed, crawl gets a `failed` row with the note `upstream_places_failed` and makes no calls.
  - A missing key records that step as failed with the note `missing_setting=…`.
  - Statuses are read back from `run_log` by `_status` (`:21`), not taken from memory.
- `_exit_code` (`:94`) returns 1 if any step failed. `capped` and `partial` only produce warnings.
- `report.py` builds everything with SQL for one run_id. The Sodra lookup takes the latest landed file at or before the run, which handles duplicate-sha runs.

Proving tests: `tests/test_extract_all.py` (`test_e2e_recorded` is the full pipeline over recordings, plus `test_crawl_skipped_when_places_failed` and `test_all_missing_firecrawl_key_fails_only_crawl`), and `tests/test_report.py`.

Hands-on:
```bash
uv run python -m src.extract report          # real DB: Sodra section real, places/crawl n/a
uv run python scripts/demo_report.py         # regenerates output/demo-recorded/extract_report.md from recordings
```

---

## 3. Invariants to check

| Invariant | Enforced at | Test |
|---|---|---|
| Keys never in cache keys or cache files | `cache.py:59`, `DiskCache.put` | `test_api_key_not_in_hash_or_cache_files` |
| Keys never in error output | `settings.py:12-13, :84` | `test_validation_error_hides_secret_values` |
| Places budget never exceeded, retries included | `_attempt` cap check `cache.py:226` | `test_cap_counts_retries` |
| Firecrawl budget never exceeded, retries included | `run_crawl` sets `max_requests` | `test_cap_cannot_be_exceeded_by_retries_within_a_scrape` |
| Exactly one run_log row per step, even on crash | `db.py:116` `finally` | `test_step_writes_failed_on_exception` |
| Append-only raw tables | no UPDATE/DELETE in `src/` | `git grep` (Stop 2) |
| Fetched data is never dropped on cap or error | `places.py` `CapWithPartial` + `finally` | `test_cap_mid_chain_lands_fetched_pages`, `test_auth_error_mid_category_lands_fetched_rows` |
| One bad item never fails the run | per-item error rows | `test_error_row_and_continue`, `test_bad_port_does_not_abort_targets`, `test_firecrawl_403_is_item_error_and_next_site_scraped` |
| robots.txt honoured before every scrape | `run_crawl` → `checker.allowed` | `test_robots_disallow_skips_without_firecrawl_call`, `test_robots_bom_disallow_all` |
| Sodra lands all or nothing | validate before a single INSERT in a transaction | `test_validation_fails_on_gap_month` |
| Deterministic given the same cache | sorted iteration, `retrieved_at` from fetch time | `test_rows_carry_page_retrieved_at_and_hash` |
| No network in tests | every test injects `MockTransport` / replay | run `uv run pytest` with Wi-Fi off |

---

## 4. Exercises: break it and watch a test catch it

These are the fastest way to check that you understand each part. Revert with `git checkout -- src/` after each one.

1. In `cache.py` `_attempt`, delete the two-line cap check at the top. `test_cap_raises_before_send` and `test_cap_counts_retries` should fail.
2. In `db.py` `step()`, move the `insert_rows` call out of `finally`. `test_step_writes_failed_on_exception` should fail.
3. In `places.py`, change `MAX_RESULTS` to `PAGE_SIZE * 2`. The saturation tests should fail.
4. In `robots.py`, change `utf-8-sig` to `utf-8`. `test_robots_bom_disallow_all` should fail.
5. In `crawl.py` `run_crawl`, delete the `fc_client.max_requests = …` line. The retry-cap test should fail.
6. In `sodra.py` `sniff`, use `csv.Sniffer` only. The real-header tests should fail, because Sniffer picks `,`.

---

## 5. What is NOT verified yet

- **No live Places or Firecrawl run has happened.** The request count (estimated at 600–1,200), the Places cost and real crawl coverage are unknown until you run `python -m src.extract all` with real keys.
- **The 10 recorded homepages are the only real Firecrawl responses seen.** No legal-candidate page has been fetched for real.
- **`SODRA_URL` is unset.** Downloading by URL is tested only against mocks; the real file came in through `SODRA_FILE`.
- **The demo report** (`output/demo-recorded/`) uses synthetic errors for unrecorded sites, so its crawl numbers are not real coverage.
