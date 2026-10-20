# Lead extraction

## How to run

Places extraction (needs `GOOGLE_API_KEY` in `.env`):

```
uv run python -m src.extract.places                       # all categories
uv run python -m src.extract.places --category cafes_restaurants      # repeatable
uv run python -m src.extract.places --run-id 2026-10_20261019T141502   # reuse a run id
```

Prints JSON log lines, then one `places_summary` line per category (`places`, `unique_places`,
`with_website`, `saturated_tiles`, `coverage_gaps`). Exit code 1 if the step failed, 2 on an
unknown category. A capped or partial run prints a `warning` line.

## Orchestrated run

```
uv run python -m src.extract all|sodra|places|crawl|report
```

`all` runs sodra, places and crawl under one run_id; `report` writes the data-quality report
from `raw.*` (offline, no API keys needed); `sodra` needs no API keys either. `crawl` and `places`
need `FIRECRAWL_API_KEY` / `GOOGLE_API_KEY`; under `all` a missing key fails only that step
(`missing_setting=<NAME>` in `run_log.notes`).

## Modelling

dbt (DuckDB) turns `raw.*` into matched places. Run from the repo root; `DB_PATH` defaults to
`data/leads.duckdb`, and there are no dbt packages to install:

```
uv run dbt build --project-dir dbt --profiles-dir dbt                # seeds, models, tests
uv run dbt build --project-dir dbt --profiles-dir dbt --select int_places_matched+   # one model
```

`python -m src.pipeline` runs `extract all`, then the same `dbt build`. dbt is skipped and the
exit code is non-zero if extraction failed; on a dbt failure the last 40 lines of its output go to stderr.

Models:

- `stg_places` (view): one row per place and category from each month's selected places run.
- `stg_web_pages` (view): crawled pages (markdown, status, skip reason) of the selected run.
- `stg_sodra` (view): Sodra companies of the selected Sodra run, with trimmed `jar_code` and EVRK.
- `category_evrk` (seed): EVRK code prefixes that count as a fit for each category.
- `int_run_pairs`: per places month, the Sodra run to join (same month, else latest earlier).
- `int_page_code_hits`: every standalone 9-digit number on a page, with its label context.
- `int_page_company_codes`: labelled company codes found on pages that exist in Sodra.
- `int_page_company_codes_unmatched`: labelled codes found on pages that are not in Sodra.
- `int_places_matched`: one row per place and month with `match_status`, `match_confidence`
  and the attached Sodra company.

DuckDB allows one writer: do not run dbt while extraction is running (and vice versa).

Match rate on the current DB:

```sql
select run_month, match_status, match_confidence, count(*)
from intermediate.int_places_matched
group by all
order by all;
```

```
run_month  match_status       match_confidence  count
2026-10    code_not_in_sodra  NULL               5
2026-10    matched            high              21
2026-10    matched            low                1
2026-10    matched            medium            21
2026-10    no_code_found      NULL              68
2026-10    not_fetched        NULL              55
```

## Sodra (Cloudflare workflow)

The Sodra download sits behind Cloudflare, so fetch the zip in a browser from
https://atvira.sodra.lt/imones/rinkiniai/index.html and set `SODRA_FILE=/path/to/file.zip`
(or `SODRA_URL` when a direct link works).

## Firecrawl cost and time

- 1 credit per returned page; failed scrapes are free.
- The free plan has 1,000 credits/month at 10 scrapes/min, so 600 pages take about 60 min.
- `MAX_FIRECRAWL_PAGES` (default 600) counts network attempts, retries included; cache hits are free.

## Tests

```
uv run pytest            # offline; committed fixtures only
LIVE=1 uv run pytest     # also runs the live tests (spends API quota)
```

Recorded tests (`@pytest.mark.recorded`) skip unless `tests/fixtures/recorded/` exists; create it
with `scripts/record_fixtures.py` (live calls).

## Cache

Responses are cached under `data/cache/<source>/` with a 30-day TTL (Places, Firecrawl), so two
runs within 30 days are served from cache. Clear `data/cache/<source>/` to refresh, and do so
after changing `FIELD_MASK`.

## Cost per run

- Places API (New) Text Search with a field mask including `websiteUri`, `rating`, `priceLevel`
  and `regularOpeningHours` bills at the Enterprise SKU.
- 1,000 free events per month, then $35 per 1,000. Each page fetched is 1 request.
- Cost = max(0, requests - 1000) x $0.035.
- Estimate for Vilnius at `tile_km` 8.0: 600-1,200 requests.
- Measured: TBD after first live run.
- `MAX_PLACES_REQUESTS` (default 1000) stops the run with status `capped`.
