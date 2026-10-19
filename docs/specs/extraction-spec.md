# Extraction Step — Spec & Task List

Scope: the **extract** layer only. Pull raw data from three sources and land it **unmodified** in DuckDB `raw.*` tables, stamped per run. No parsing beyond what is needed to land it, and no matching, scoring or code extraction (those are downstream).

Sources:
1. Google Places API (New): visible activity
2. Firecrawl: websites of the listings, the input for entity matching
3. Sodra monthly export: declared activity

---

## 0. Conventions (apply to every task)

| Topic | Rule |
|---|---|
| Language | Python 3.12, `uv` or `pip` via `pyproject.toml` |
| DB | `data/leads.duckdb`, schema `raw`. Single writer, steps run sequentially |
| Run identity | `run_month` = `YYYY-MM` (target month, from env `RUN_MONTH`, defaults to the current month). `run_id` = `{run_month}_{UTC yyyymmddTHHMMSS}`. A re-run of the same month creates a new `run_id`; downstream uses the latest `run_id` per month |
| Append-only | Raw tables are never updated or deleted. Every row carries `run_id` and `retrieved_at` (UTC) |
| Raw = raw | Store the source payload as-is (JSON / text) plus a few routing columns. Parsing happens in dbt staging |
| Secrets | `.env`: `GOOGLE_API_KEY`, `FIRECRAWL_API_KEY`. Never logged, never committed. Provide `.env.example` |
| Budget guards | Env `MAX_PLACES_REQUESTS` (default 300) and `MAX_FIRECRAWL_PAGES` (default 600). Hitting a cap stops that extractor cleanly and records `status = 'capped'` in `run_log` |
| Retries | Retry 429/5xx/timeouts with exponential backoff and jitter, max 3 attempts. A 4xx other than 429 does not retry |
| Failure isolation | A failure on one item (place, URL) is recorded as an error row and does not fail the run. A failure of a whole source fails that step, with non-zero exit |
| Logging | Structured (JSON lines) to stdout: step, run_id, counts, durations. No payload dumps |
| Network in tests | None. Tests use recorded fixtures in `tests/fixtures/` |
| Determinism | Same config plus the same cache gives the same raw rows (apart from `run_id` / `retrieved_at`) |

---

## 1. Shared data contracts

### 1.1 `raw.run_log`
| column | type | notes |
|---|---|---|
| run_id | VARCHAR | |
| run_month | VARCHAR | `YYYY-MM` |
| step | VARCHAR | `places` / `crawl` / `sodra` |
| started_at, finished_at | TIMESTAMP | UTC |
| status | VARCHAR | `ok` / `partial` / `capped` / `failed` |
| items_ok, items_failed | INTEGER | |
| api_requests | INTEGER | billable calls actually sent (excludes cache hits) |
| cache_hits | INTEGER | |
| notes | VARCHAR | short free text, e.g. cap reached |

### 1.2 `raw.places`
| column | type | notes |
|---|---|---|
| run_id, retrieved_at | | |
| category | VARCHAR | key from `categories.yaml` |
| tile_id | VARCHAR | grid tile that returned it |
| request_hash | VARCHAR | hash of the request body, for cache traceability |
| place_id | VARCHAR | Google `id` |
| payload | JSON | full place object as returned |

Uniqueness: `(run_id, category, place_id)`. The same place can legitimately appear under two categories; keep both rows.

### 1.3 `raw.places_errors`
`run_id, retrieved_at, category, tile_id, request_hash, http_status, error`

### 1.4 `raw.web_pages`
| column | type | notes |
|---|---|---|
| run_id, retrieved_at | | |
| place_id | VARCHAR | listing this page was fetched for |
| page_role | VARCHAR | `home` / `legal_candidate` |
| requested_url | VARCHAR | |
| final_url | VARCHAR | after redirects |
| http_status | INTEGER | |
| skip_reason | VARCHAR | null if fetched; else `robots_disallow` / `social_domain` / `no_website` / `capped` / `error` |
| markdown | VARCHAR | Firecrawl markdown, null if skipped |
| links | JSON | outbound links from the page (home only) |
| content_sha256 | VARCHAR | of the markdown |

One row per attempted (place_id, URL). Listings without a website still get one row with `skip_reason = 'no_website'`, so the coverage denominator stays honest.

### 1.5 `raw.sodra`
| column | type | notes |
|---|---|---|
| run_id, retrieved_at | | |
| source_url | VARCHAR | exact file URL downloaded |
| file_sha256 | VARCHAR | of the zip |
| data_month | VARCHAR | `month` value from the file (yyyymm) |
| code, jarCode, name, municipality, ecoActCode, ecoActCodeStr, ecoActName, month, avgWage, numInsured, avgWage2, numInsured2, tax | VARCHAR | **all as text**; typing happens in staging (keeps leading zeros, empty → NULL handled downstream) |

Column names come from the English token in parentheses in the source header, e.g. `Draudėjo kodas (code)` → `code`.

---

## 2. Config: `config/categories.yaml`

```yaml
area:
  name: Vilnius
  # approximate city bounding box; verify and adjust
  bbox: {south: 54.57, west: 25.02, north: 54.83, east: 25.48}
  tile_km: 2.0                 # initial grid tile size

categories:
  cafes_restaurants:
    places_included_type: [cafe, coffee_shop, restaurant]
    evrk_prefixes: ["56.1", "56.3"]   # verify against Sodra ecoActCodeStr values
  hair_beauty:
    places_included_type: [hair_salon, barber_shop, beauty_salon]
    evrk_prefixes: ["96.02"]
  car_service:
    places_included_type: [car_repair, car_wash]
    evrk_prefixes: ["45.2"]
```

The EVRK prefixes are used downstream, not in extraction. They live here so there is one definition of "category". Verify the codes against the distinct `ecoActCodeStr` values in the Sodra file, since activity-code revisions may have changed them.

---

## 3. Task list

Each task should take roughly 20–45 minutes for an agent. IDs are referenced in commits (`EXT-03: ...`).

### EXT-01 · Project skeleton & settings
**Depends on:** none
**Do:**
- `pyproject.toml` with deps: `httpx`, `duckdb`, `pyyaml`, `pydantic-settings`, `tenacity`, `firecrawl-py`, `pytest`.
- `src/settings.py`: load env and `categories.yaml` into typed settings, and fail fast on a missing key or invalid bbox.
- `src/run.py`: create `run_id` / `run_month` and expose them to all steps.
- `.env.example`, `data/` in `.gitignore`.

**Done when:** `python -m src.settings` prints the resolved config (keys masked), and the unit test for `run_id` format passes.

### EXT-02 · DuckDB raw schema & writer
**Depends on:** EXT-01
**Do:**
- `src/db.py`: connect, `CREATE SCHEMA IF NOT EXISTS raw`, and create all tables from §1 idempotently.
- Batch insert helper that adds `run_id` / `retrieved_at` automatically.
- `run_log` helpers: `start_step()` and `finish_step(status, counts)`.

**Done when:** creating the schema twice is a no-op; a test inserts and reads one row per table; `finish_step` is written even when the step raises (use try/finally).

### EXT-03 · HTTP cache & rate limiter
**Depends on:** EXT-01
**Do:**
- `src/cache.py`: on-disk cache under `data/cache/{source}/`, keyed by a hash of the method, URL and body. Configurable TTL per source: Places 30 days, Firecrawl 30 days, Sodra none (the file checksum dedupes).
- Simple token-bucket limiter per source (Places 5 rps, Firecrawl per plan limit, default 2 rps).
- Counters for `api_requests` and `cache_hits`.

**Done when:** the second identical request in a test returns from cache without a network call (assert with a mock transport).
**Note:** the Places cache TTL is a deliberate ToS-aware choice. Record it in DECISIONS.md.

### EXT-04 · Places: tiling & search
**Depends on:** EXT-02, EXT-03
**Do:**
- Split `bbox` into tiles of `tile_km`.
- For each (category, tile), call Text Search (New) `POST https://places.googleapis.com/v1/places:searchText` with:
  - `textQuery` = a category-neutral phrase, or omit it in favour of `includedType`; check which the API requires.
  - `includedType` for each type in `places_included_type`. One type per request; the API takes a single `includedType`.
  - `locationRestriction.rectangle` = the tile.
  - `languageCode: "lt"`, `regionCode: "LT"`.
  - Header `X-Goog-FieldMask` limited to: `places.id, places.displayName, places.formattedAddress, places.location, places.types, places.primaryType, places.websiteUri, places.userRatingCount, places.rating, places.priceLevel, places.regularOpeningHours, places.businessStatus, places.googleMapsUri, nextPageToken`.
- Paginate with `nextPageToken` up to the API maximum.
- **Saturation rule:** if a (type, tile) returns the maximum number of results (all pages full), split the tile into 4 and re-query, down to a minimum tile of 0.5 km. Log saturated tiles at the minimum size as a known coverage gap.
- Dedupe `place_id` within (run_id, category) before insert.
- Write errors to `raw.places_errors`.

**Done when:**
- A fixture-based test covers pagination, tile split on saturation, and dedupe.
- A live smoke run on one tile and one type lands rows.
- `run_log` shows the request count.

**Check before building:** current field-mask → SKU pricing (rating, hours and website fields may move the request into a higher tier). Document the expected cost per run in the README.

### EXT-05 · Places: run all categories
**Depends on:** EXT-04
**Do:** a CLI `python -m src.extract.places` that loops over categories and respects `MAX_PLACES_REQUESTS`. Print a summary per category: places, unique places, places with a website, saturated tiles.
**Done when:** a full Vilnius run completes within budget, and the summary numbers equal the matching `SELECT count(*)` queries.

### EXT-06 · Crawl: target selection
**Depends on:** EXT-02
**Do:**
- `src/extract/crawl.py::targets(run_id)`: read `raw.places` for the run, extract `websiteUri` from the JSON, and normalise it (lowercase host, strip tracking parameters and fragments).
- Classify each listing:
  - `no_website`
  - `social_domain`: facebook, instagram, linktr.ee, wolt, bolt, tiktok, google sites, booking platforms; keep the list in config.
  - `crawl`
- Dedupe by normalised URL: chains share sites, so scrape once and map the result to all their place_ids.

**Done when:** a unit test on about 15 sample URLs gives the expected classes, and the dedupe maps several place_ids to one URL.

### EXT-07 · Crawl: robots & homepage scrape
**Depends on:** EXT-03, EXT-06
**Do:**
- Before any scrape, fetch `robots.txt` (cached) and check it with `urllib.robotparser` for the declared user agent. If disallowed, record `skip_reason = 'robots_disallow'`.
- Scrape the homepage via Firecrawl (`formats: markdown + links`, main-content-only **off**, because legal details live in the footer).
- Land one row per place_id that maps to the URL.

**Done when:** fixture tests cover allow, disallow and error paths; a live run on 5 sites lands markdown with footers present (spot check).

### EXT-08 · Crawl: legal-details candidate pages
**Depends on:** EXT-07
**Do:**
- From the homepage `links`, pick up to **2** same-domain links whose path or anchor text matches (case-insensitive) `kontakt|rekvizit|apie|about|contact|privatum|privacy|taisykl|terms|imone|company`.
- Rank the links: `rekvizit` first, then `kontakt`, then the rest.
- Scrape them with the same rules as EXT-07, with `page_role = 'legal_candidate'`.
- Respect `MAX_FIRECRAWL_PAGES` across both page roles.

**Done when:** tests on fixture link lists choose the expected pages, and the cap stops cleanly with `status = 'capped'`.

### EXT-09 · Sodra: download
**Depends on:** EXT-02
**Do:**
- Start from `https://atvira.sodra.lt/imones/rinkiniai/index.html`. Find the link for the **monthly CSV zip** by inspecting the page (the agent must look at the page and must not guess the file name). Put the resolved URL pattern in config, with a fallback env `SODRA_URL`.
- Download, compute `sha256`, and store under `data/raw/sodra/{sha}.zip`.
- If the same sha was already landed, skip the insert but still log the step as `ok` with a note.

**Done when:** a repeat download is detected as a duplicate, and `source_url` and `file_sha256` are recorded.

### EXT-10 · Sodra: parse & land
**Depends on:** EXT-09
**Do:**
- Unzip and detect the encoding (expect UTF-8, possibly with BOM) and the delimiter (sniff it; the sample suggests a tab or semicolon).
- Map headers to the English token in parentheses.
- Load **all rows unfiltered** as VARCHAR into `raw.sodra`. Use DuckDB `read_csv` with `all_varchar=true` for speed.
- Validate before committing:
  - Row count is above 50,000 (a sanity floor; adjust after the first look).
  - All expected columns are present.
  - Exactly one distinct `month`.
  - The share of non-empty `jarCode` values that are 9 digits is ≥ 99%.
  - `numInsured` is parseable as an integer where non-empty.

  Fail the step if a check fails.

**Done when:** the validation report prints; a test with a 20-row fixture (including empty `jarCode` and empty `avgWage`, like the embassy rows) passes.

### EXT-11 · Orchestrated extract entrypoint
**Depends on:** EXT-05, EXT-08, EXT-10
**Do:** `python -m src.extract all` runs `sodra → places → crawl` in sequence, under one `run_id`. Exit non-zero if any step has `status = 'failed'`; `partial` and `capped` are warnings. Wire it into `src/pipeline.py`.
**Done when:** one command lands all raw tables for a run, and `run_log` has 3 rows for that `run_id`.

### EXT-12 · Extraction data-quality report
**Depends on:** EXT-11
**Do:** `src/extract/report.py` prints and writes `output/{run_month}/extract_report.md` with:
- Places per category.
- Share of places with a website, share crawled, and skip reasons broken down.
- Saturated tiles.
- Sodra rows and the share with `jarCode`.
- API requests and cache hits per source.
- Run duration.

**Done when:** the report is generated from DuckDB queries only, with no in-memory state, and is committed for the example run.

---

## 4. Out of scope for this step
- Company code / VAT extraction from page text (match step, `src/match/code_extract.py`)
- Typing and cleaning (dbt staging)
- JAR (dropped: the Sodra file includes zero-employee companies; noted as a production addition)
- Scheduling: monthly runs are triggered manually in this version

## 5. Open questions to resolve during implementation
1. Places Text Search: is `textQuery` required alongside `includedType`, and what is the exact page and result maximum? Confirm in the current docs.
2. The SKU tier triggered by the chosen field mask, and the resulting cost per full run.
3. Firecrawl: which endpoint and SDK version to use, how JS-rendered sites behave, and whether it honours robots.txt itself (keep our own check regardless).
4. The exact Sodra monthly file URL and its delimiter.
