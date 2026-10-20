# Modelling Step — Spec

Scope: the **dbt** layer from `raw.*` up to one intermediate model: **one row per Google place, with the Sodra company it belongs to attached when one can be found**. The lead list (scoring, filtering, export) is built on top of this later and is out of scope here.

The only matching key is the company code (`jarCode`, 9 digits) found as text on the place's crawled pages. Name matching and address matching are deliberately **not** done (see §6).

Upstream contract: `docs/specs/extraction-spec.md` §1 (raw tables) and §2 (`config/categories.yaml`).

---

## 0. Conventions

| Topic | Rule |
|---|---|
| Tooling | `dbt-core` + `dbt-duckdb`, added to `pyproject.toml` and run through `uv run dbt ...`. Pin to the current minor versions at implementation time |
| Location | dbt project in `dbt/` (`dbt_project.yml`, `models/`, `seeds/`, `tests/`, `macros/`). `profiles.yml` lives in `dbt/` too and is committed; it holds no secrets |
| Database | The same DuckDB file as extraction (`DB_PATH`, default `data/leads.duckdb`). The profile reads the path from env `DB_PATH`, falling back to `../data/leads.duckdb` relative to `dbt/` |
| Schemas | `staging` (views) and `intermediate` (tables) in that file. `raw` is read-only for dbt and is declared as a dbt source |
| Materialisation | All `stg_*` models are **views**. `int_*` models are **tables** (they run regexes over markdown; rebuilding is cheap at this size) |
| Naming | `stg_<source>` for staging, `int_<entity>_<verb>` for intermediate. Columns are `snake_case` |
| Run selection | "Downstream uses the latest `run_id` per month" (extraction spec §0). Staging resolves this per source, see §2 |
| Network | None. dbt reads only what is already landed |
| Tests | Every model has a `schema.yml` with column tests. Code-extraction logic is covered by dbt **unit tests** (fixed input rows, expected output rows), so behaviour is pinned without the real DB |
| Pipeline | `src/pipeline.py` runs `dbt build` after extraction (replacing the current "not implemented" log line) and exits non-zero if dbt fails. Export stays not implemented |

---

## 1. Sources (`dbt/models/staging/_sources.yml`)

Source `raw` with tables `places`, `places_errors`, `web_pages`, `sodra`, `run_log`. Column descriptions copied from the extraction spec. Source-level tests:
- `raw.places`: `(run_id, category, place_id)` unique.
- `raw.web_pages`: `place_id`, `page_role`, `run_id` not null.
- `raw.sodra`: `month`, `code` not null.

---

## 2. Run selection

A run counts as usable for a step when its `raw.run_log` row for that step has `status <> 'failed'`. (`partial` and `capped` are usable: they hold real rows. The current demo places run is `partial`.)

- **Places and web pages** come from one run: the crawl step reuses the places `run_id`. The selected run is the greatest `run_id` that has a usable `places` row in `run_log`. `run_id` sorts by time because it starts with `YYYY-MM_` followed by a UTC timestamp. Web pages are taken from that same `run_id`. If its crawl step has not run, web pages are simply empty.
- **Sodra** is selected independently: the greatest `run_id` with a usable `sodra` row.

Implement this once as a macro, `latest_run(step)`, that returns a subquery yielding a single `run_id`. Staging models filter with `where run_id = (<latest_run>)`.

---

## 3. Staging models (views)

### 3.1 `stg_places`
Grain: one row per `(place_id, category)` for the selected run. A place found under two categories keeps both rows, as in raw.

| column | from |
|---|---|
| place_id | `place_id` |
| category | `category` |
| name | `payload.displayName.text` |
| primary_type | `payload.primaryType` |
| types | `payload.types` as `VARCHAR[]` |
| address | `payload.formattedAddress` |
| lat, lng | `payload.location.latitude/longitude` as `DOUBLE` |
| rating | `DOUBLE`, null if absent |
| user_rating_count | `INTEGER`, null if absent |
| website_uri | `payload.websiteUri`, null if absent |
| business_status | `payload.businessStatus` |
| google_maps_uri | `payload.googleMapsUri` |
| run_id, retrieved_at | passthrough |

If the same `(place_id, category)` appears on more than one tile in a run, keep one row (earliest `retrieved_at`). Raw uniqueness already prevents this, but the model must not rely on it.

Tests: `(place_id, category)` unique; `place_id`, `name`, `category` not null; `category` in the keys of `categories.yaml`.

### 3.2 `stg_web_pages`
Grain: one row per raw row for the selected run, so the coverage denominator stays honest.

Columns: `place_id`, `page_role`, `requested_url`, `final_url`, `http_status`, `skip_reason`, `is_fetched` (`skip_reason is null and markdown is not null`), `markdown`, `content_sha256`, `run_id`, `retrieved_at`. `links` is dropped because nothing downstream uses it yet.

Tests: `page_role` in (`home`, `legal_candidate`); `skip_reason` in (`robots_disallow`, `social_domain`, `no_website`, `capped`, `error`) or null; `place_id` has a relationship to `stg_places.place_id`.

### 3.3 `stg_sodra`
Grain: one row per `(jar_code, month)` for the selected run. Rows with a null `jarCode` (about 5k rows: embassies and other insurers that have no company code) are **dropped**, because they can never match.

| column | type | rule |
|---|---|---|
| jar_code | VARCHAR | `jarCode`, trimmed, kept as text (leading zeros) |
| sodra_code | VARCHAR | `code` (Sodra's insurer code, not the company code) |
| company_name | VARCHAR | `name` |
| municipality | VARCHAR | |
| evrk_code | VARCHAR | `ecoActCodeStr` (e.g. `56.11.00`) |
| evrk_name | VARCHAR | `ecoActName` |
| month | DATE | `month` (`yyyymm`) → first day of that month |
| avg_wage | DECIMAL(12,2) | `avgWage`; empty string → null |
| num_insured | INTEGER | `numInsured`; empty → null |
| avg_wage_2 | DECIMAL(12,2) | `avgWage2`; empty → null |
| num_insured_2 | INTEGER | `numInsured2`; empty → null |
| tax | DECIMAL(14,2) | `tax`; empty → null |
| run_id, data_month | passthrough |

Use `try_cast`. A value that is non-empty but fails to cast becomes null, and a test (`warn` severity) counts those rows so they are not silently lost.

Tests: `(jar_code, month)` unique; `jar_code` matches `^\d{9}$`.

`stg_places_errors` and `stg_run_log` are not built: nothing downstream needs them yet.

### 3.4 Seed: `category_evrk`
`dbt/seeds/category_evrk.csv` with columns `category,evrk_prefix`, one row per prefix in `config/categories.yaml`. dbt cannot read the YAML directly, so the seed duplicates it. A **pytest** test asserts that the seed matches `categories.yaml` exactly, which keeps one effective definition of "category".

---

## 4. Intermediate models (tables)

### 4.1 `int_page_company_codes`
Grain: one row per `(place_id, jar_code)` found on that place's fetched pages.

Extraction, from `stg_web_pages` where `is_fetched`:
1. Find every standalone 9-digit number: `\b\d{9}\b`, matched on the markdown text. `LT` + 9 digits (VAT) does not match because there is no word boundary between `T` and the digit. This is intended: VAT is not the company code.
2. For each hit, look at the 40 characters before it. The hit is **labelled** when that window matches `(?i)(įmonės|imones)\s+kodas|\bkodas\b|įm\.\s*k\.|company\s+code|juridinio asmens kodas`.
3. Keep only hits that exist in `stg_sodra.jar_code` (any month of the selected run). This validation removes phone numbers (`8 6xx xxxxx` style), order numbers and similar noise.

Columns: `place_id`, `jar_code`, `is_labelled` (true if any occurrence was labelled), `n_occurrences`, `found_on_roles` (`VARCHAR[]` of distinct `page_role`), `first_url` (the `final_url` of one page it was found on, preferring `legal_candidate`).

Also build `int_page_company_codes_unmatched` (table): the **labelled** hits that are **not** in `stg_sodra`, with the same columns. It is the list of "code shown on the site, but company not in this Sodra file", used for `match_status` and for diagnosing coverage.

Unit tests (dbt `unit_tests`), each a few hand-written markdown rows:
- `Įmonės kodas: 305693312` → one labelled hit.
- `**Įmonės kodas:** 305693312` (markdown bold) → labelled.
- `| Įmonės kodas | 121982951 |` (table cell) → labelled.
- `PVM kodas LT100012345678` and `LT123456789` → no hit.
- `Tel. 860012345` with no label, and not in the Sodra fixture → no hit.
- Same code on the home page and a legal page → one row, `n_occurrences = 2`, both roles.
- Two different valid codes on one place → two rows.

### 4.2 `int_places_matched` — the deliverable
Grain: **one row per `place_id`** of the selected run (all of them, matched or not). Today that is 171 rows.

Building it:
1. **Places → one row.** Aggregate `stg_places` by `place_id`: `categories` = sorted `VARCHAR[]` of its categories; every other place attribute from any row (they are identical across categories within a run).
2. **Crawl coverage.** From `stg_web_pages`: `crawl_status` =
   - `no_website` / `social_domain` / `robots_disallow` / `capped` / `error`, the home row's `skip_reason`, when the home page was not fetched;
   - `fetched` when the home page was fetched;
   - `not_crawled` when the place has no web page rows at all.
   
   Plus `pages_fetched` (count of fetched rows).
3. **Pick one code.** From `int_page_company_codes`, choose per place by: labelled first, then the most occurrences, then found on a `legal_candidate` page, then the lowest `jar_code` (for determinism). Record `n_candidate_codes` (the count of valid codes found) so ambiguous places stay visible.
4. **Attach Sodra.** Join the chosen `jar_code` to the company's **latest month** in `stg_sodra`.

Columns:

| group | columns |
|---|---|
| place | `place_id`, `name`, `categories`, `primary_type`, `address`, `lat`, `lng`, `rating`, `user_rating_count`, `website_uri`, `business_status`, `google_maps_uri` |
| crawl | `crawl_status`, `pages_fetched` |
| match | `match_status` (`matched` / `code_not_in_sodra` / `no_code_found` / `not_fetched`), `jar_code`, `code_is_labelled`, `n_candidate_codes`, `code_source_url` |
| company (latest month) | `company_name`, `municipality`, `evrk_code`, `evrk_name`, `sodra_month`, `num_insured`, `avg_wage`, `tax` |
| checks | `evrk_matches_category`, true when `evrk_code` starts with a prefix of any of the place's categories (via the `category_evrk` seed); null when unmatched |
| lineage | `places_run_id`, `sodra_run_id` |

`match_status` rules:
- `not_fetched` when `crawl_status <> 'fetched'`;
- else `matched` when a code was chosen;
- else `code_not_in_sodra` when the place has rows in `int_page_company_codes_unmatched`;
- else `no_code_found`.

Only `matched` rows have company columns.

Tests:
- `place_id` unique and not null;
- row count equals the distinct `place_id` count in `stg_places` (a custom singular test);
- `match_status` accepted values;
- `jar_code` is not null exactly when `match_status = 'matched'`;
- `jar_code` has a relationship to `stg_sodra.jar_code`.

Expected on the current data, as a sanity check rather than a test:
- 116 fetched;
- about 40–43 `matched`;
- 1 place with `n_candidate_codes > 1`;
- 55 `not_fetched` (29 `no_website`, 20 `social_domain`, 4 `robots_disallow`, 2 `error`).

### 4.3 Not in this step
- Monthly Sodra history (trend over the 8 months) per matched company. `stg_sodra` keeps all months, so a later lead model can compute it directly.
- Deduplicating two places that resolve to the same `jar_code` (chains). They stay as separate rows. The lead step decides whether that is one lead or many.

---

## 5. How it runs

```bash
uv run dbt deps   --project-dir dbt --profiles-dir dbt   # only if packages are used
uv run dbt build  --project-dir dbt --profiles-dir dbt   # seeds, models, tests
```

- `src/pipeline.py` calls the same `dbt build` through `subprocess` after `extract all`. It skips dbt and exits non-zero if extraction failed.
- The README gets a short "Modelling" section: the commands above, what each model is, and the match-rate query.
- DuckDB allows one writer. dbt and the extractor must not run at the same time. Document this in the README; no locking is added.

If packages are needed (e.g. `dbt_utils` for `unique_combination_of_columns`), use `dbt_utils` only. Otherwise write the few generic tests needed in `dbt/tests/generic/`.

---

## 6. Out of scope (decided)
- **Name matching** (Google name vs Sodra legal name): brand and legal names usually differ (e.g. "UN MÈTRO Cafe-Bar" vs a UAB name), so it gives low precision. Dropped.
- **Address matching:** Sodra has no street address, only the municipality. It would need the JAR register, which the extraction spec already dropped.
- The lead list, scoring, filtering and export.
- Cross-run history models (comparing months).

## 7. Open questions to settle during implementation
1. Check that the DuckDB regex functions (RE2) support `\b`. If not, use `(^|[^0-9A-Za-z])(\d{9})($|[^0-9])` and extract group 2. Unit tests in §4.1 must pass either way.
2. dbt unit tests need a `dbt-core` version ≥ 1.8. Confirm the pinned version supports them on `dbt-duckdb`.
3. Check that the label window of 40 characters catches the table-cell layout (`| Įmonės kodas | 121982951`) on the real pages. If not, widen it and record the value used.
