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
uv run dbt build --project-dir dbt --profiles-dir dbt --select +int_places_matched   # that model and everything upstream
```

`python -m src.pipeline` runs `extract all`, then the same `dbt build`. dbt is skipped and the
exit code is non-zero if extraction failed; on a dbt failure the last 40 lines of its output go to stderr.

Models:

- `stg_places` (view): one row per place and category from each month's selected places run.
- `stg_web_pages` (view): crawled pages (markdown, status, skip reason) of the selected run.
- `stg_sodra` (view): Sodra companies of each month's selected Sodra run, with trimmed `jar_code` and EVRK.
- `category_evrk` (seed): EVRK code prefixes that count as a fit for each category.
- `int_run_pairs`: per places month, the Sodra run to join (same month, else latest earlier).
- `int_page_code_hits`: every standalone 9-digit number on a page, with its label context.
- `int_page_company_codes`: company codes (labelled or not) found on a place's pages that exist in the paired Sodra run.
- `int_page_company_codes_unmatched`: labelled codes found on pages that are not in Sodra.
- `int_places_matched`: one row per place and month with `match_status`, `match_confidence`
  and the attached Sodra company.

DuckDB allows one writer: do not run dbt while extraction is running (and vice versa).

Known limitations:

- Re-running `extract crawl` for the same run appends page rows again, so `pages_fetched` and
  `n_occurrences` are inflated (status and confidence are usually unchanged). Deduplication is a planned spec change.
- Staging views reference the DB file name as their catalog (dbt-duckdb behaviour). After copying or
  renaming the `.duckdb` file, re-run `dbt build` before querying `staging.*`.

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

## Leads

`marts.mart_leads` has one row per matched company per monthly run, with a tier, the numbers behind
it and a one-sentence reason (`lead_reason`). A lead is a reason to look at a business, not proof of
anything: the list says where checking is most worthwhile, and the analyst decides.

A company is flagged only when two independent signals agree: it is **busy in public** (many Google
reviews per insured employee, compared with similar businesses) and it **declares low pay** (average
wage close to the minimum wage). Low pay alone never makes a lead, because part-time work is legal.

| Tier | Meaning for the analyst |
|---|---|
| `flag` | Look here first: busy in public and low declared pay. |
| `watch` | Busy in public, but pay looks normal or is not published (Sodra hides it for very small companies). |
| `clear` | Nothing stands out. Includes categories with too few comparable companies to judge "busy". |
| `excluded` | Not assessed; `exclusion_reason` says why (weak company match, place not operational, no employee data, too few reviews). |

Thresholds are dbt vars in `dbt/dbt_project.yml`:

| Var | Value | Meaning |
|---|---|---|
| `min_monthly_wage` | `1153` | Lithuanian minimum monthly wage, EUR, 2026. Update yearly |
| `low_wage_margin` | `0.10` | "near" the minimum: average wage at most 1153 x 1.10 = 1268.30 |
| `busy_multiple` | `2.0` | busy = at least 2x the category median of reviews per employee |
| `min_peers` | `5` | fewest eligible companies a category needs for its median to count |

and per category in the seed `dbt/seeds/category_lead_rules.csv`: `min_reviews` (fewer reviews than
this and the company is excluded: 200 cafés and restaurants, 100 hair and beauty, 50 car service), a
`caveat` shown on the lead card and a display `label`. To try another value without editing files, override a var on the command line (or edit the vars
or the seed and rebuild):

```
uv run dbt build --project-dir dbt --profiles-dir dbt --vars '{busy_multiple: 1.5}'
```

Export the list for the latest month:

```
uv run python -m src.export [--top N] [--out DIR]
```

It writes `output/<run_month>/leads.csv` (every company) and `leads.md` (a summary, then one card
per `flag` and `watch` lead). `--top N` also adds cards for the next companies by priority under
"Closest to the threshold (not flagged)"; each card shows the company's real tier. `LEADS_TOP_N`
sets the default (10; `0` = flag and watch only), and `python -m src.pipeline` uses it. `--top` only
changes the cards, never the table or the CSV.

Queries the analyst can run on `data/leads.duckdb` (latest month, current DB):

1. Share of `flag` and `watch` per category, over all companies (excluded included) and over eligible
   companies only:

```sql
select lead_category,
       count(*) as companies,
       round(100.0 * count(*) filter (where tier = 'flag') / count(*)) as flag_pct_all,
       round(100.0 * count(*) filter (where tier = 'watch') / count(*)) as watch_pct_all,
       round(100.0 * count(*) filter (where tier = 'flag')
             / nullif(count(*) filter (where tier <> 'excluded'), 0)) as flag_pct_eligible,
       round(100.0 * count(*) filter (where tier = 'watch')
             / nullif(count(*) filter (where tier <> 'excluded'), 0)) as watch_pct_eligible
from marts.mart_leads
where run_month = (select max(run_month) from marts.mart_leads)
group by lead_category
order by lead_category;
```

```
lead_category      companies  flag_pct_all  watch_pct_all  flag_pct_eligible  watch_pct_eligible
cafes_restaurants  17         0.0           12.0           0.0                14.0
car_service        8          0.0           0.0            0.0                0.0
hair_beauty        5          0.0           0.0            0.0                0.0
```

2. New flags: companies flagged now that were not flagged last month:

```sql
select run_month, jar_code, company_name, lead_category, tier, prev_tier
from marts.mart_leads
where tier = 'flag' and prev_tier is distinct from 'flag'
order by run_month, jar_code;
```

```
(0 rows)
```

3. One company over time (here the top `watch` lead; there is only one month of data so far):

```sql
select run_month, tier, reviews_per_employee, num_insured, avg_wage
from marts.mart_leads
where jar_code = '304149432'
order by run_month;
```

```
run_month  tier   reviews_per_employee  num_insured  avg_wage
2026-10    watch  86.1                  16           1546.74
```

Known limitations:

- A category median over a handful of companies is a weak yardstick. Only cafés and restaurants reach
  `min_peers` on the demo data; hair and beauty and car service are always `clear`, never "busy".
- Reviews per employee is a rough measure: tourist-heavy places, and stylists who rent a chair as
  self-employed, distort it. The card's `Note` line says which caveat applies.

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
