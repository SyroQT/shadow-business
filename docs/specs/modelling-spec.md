# Modelling Step — Spec

Scope: the **dbt** layer from `raw.*` up to one intermediate model: **one row per Google place per monthly run, with the Sodra company it belongs to attached when one can be found**, plus how confident that match is and the evidence behind it. The lead list (scoring, filtering, export) is built on top of this later and is out of scope here.

The only matching key is the company code (`jarCode`, 9 digits) found as text on the place's crawled pages. Name matching and address matching are deliberately **not** done (see §6).

Upstream contract: `docs/specs/extraction-spec.md` §1 (raw tables) and §2 (`config/categories.yaml`).

Driven by `task.pdf`, four things shape the design:
- the analyst runs it **monthly and compares over time**, so every month is kept (§2);
- flagging an honest business is costly, so every match has a **confidence** (§4.2);
- chains must not be flagged once per branch, so **places sharing a company** are counted (§4.2);
- every lead must be **traceable** to its listing and its official record, so evidence travels with the row (§4.1, §4.2).

---

## 0. Conventions

| Topic | Rule |
|---|---|
| Tooling | `dbt-core` + `dbt-duckdb`, added to `pyproject.toml` and run through `uv run dbt ...`. Pin to the current minor versions at implementation time |
| Location | dbt project in `dbt/` (`dbt_project.yml`, `models/`, `seeds/`, `tests/`, `macros/`). `profiles.yml` lives in `dbt/` too and is committed; it holds no secrets |
| Database | The same DuckDB file as extraction (`DB_PATH`, default `data/leads.duckdb`). The profile reads the path from env `DB_PATH`, falling back to `data/leads.duckdb`, relative to the repo root (dbt is run from the repo root) |
| Schemas | `staging` (views) and `intermediate` (tables) in that file. `raw` is read-only for dbt and is declared as a dbt source |
| Materialisation | All `stg_*` models are **views**. `int_*` models are **tables** (they run regexes over markdown; rebuilding is cheap at this size) |
| Naming | `stg_<source>` for staging, `int_<entity>_<verb>` for intermediate. Columns are `snake_case` |
| Run selection | "Downstream uses the latest `run_id` per month" (extraction spec §0). Staging keeps **every month**, one run per month per source, see §2 |
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

## 2. Run selection (monthly history)

A run counts as usable for a step when its `raw.run_log` row for that step has `status <> 'failed'` **and** at least one row of that run exists in the step's raw table (`raw.places` for `places`, `raw.sodra` for `sodra`). The row check matters because a run can be logged `ok` without landing data, e.g. a duplicate Sodra file is logged `ok` with 0 rows and must not shadow the earlier run that holds the rows. (`partial` and `capped` are usable: they hold real rows. The current demo places run is `partial`.) `run_month` is the `YYYY-MM` prefix of `run_id` (and equals `run_log.run_month`).

Staging keeps **all months**, with exactly one run per month per source, so that months can be compared and an older month never changes when a new one lands:
- **Places and web pages** come from one run: the crawl step reuses the places `run_id`. For each `run_month`, the selected run is the greatest `run_id` in that month with a usable `places` row. `run_id` sorts by time because it starts with `YYYY-MM_` followed by a UTC timestamp. Web pages are taken from that same `run_id`. If its crawl step has not run, that month's web pages are simply empty.
- **Sodra**: for each `run_month`, the greatest `run_id` in that month with a usable `sodra` row.
- **Pairing a places month with Sodra**: a places month `M` uses the Sodra run selected for `M`. If no Sodra run exists for `M`, it falls back to the latest selected Sodra run with `run_month < M`. The pairing is recorded on every row (`sodra_run_id`), so a stale pairing is visible. A places month with no Sodra run at or before it gets no company data (every row `not_fetched` or `no_code_found`/`code_not_in_sodra`).

Implement this once as a macro, `selected_runs(step)`, that returns a relation `(run_month, run_id)` with one row per month. Staging models inner-join it. The pairing lives in one small intermediate model, `int_run_pairs` (`run_month`, `places_run_id`, `sodra_run_id`), that the other intermediate models use.

Every staging and intermediate model carries `run_month` as the first column of its grain.

---

## 3. Staging models (views)

### 3.1 `stg_places`
Grain: one row per `(run_month, place_id, category)` for the selected run of each month. A place found under two categories keeps both rows, as in raw.

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
| run_month, run_id, retrieved_at | passthrough |

If the same `(place_id, category)` appears on more than one tile in a run, keep one row (earliest `retrieved_at`). Raw uniqueness already prevents this, but the model must not rely on it.

Tests: `(run_month, place_id, category)` unique; `place_id`, `name`, `category` not null; `category` in the keys of `categories.yaml`.

### 3.2 `stg_web_pages`
Grain: one row per raw row for the selected run of each month, so the coverage denominator stays honest.

Columns: `place_id`, `page_role`, `requested_url`, `final_url`, `http_status`, `skip_reason`, `is_fetched` (`skip_reason is null and markdown is not null`), `markdown`, `content_sha256`, `run_month`, `run_id`, `retrieved_at`. `links` is dropped because nothing downstream uses it yet.

Tests: `page_role` in (`home`, `legal_candidate`); `skip_reason` in (`robots_disallow`, `social_domain`, `no_website`, `capped`, `error`) or null; every `(run_month, place_id)` exists in `stg_places`.

### 3.3 `stg_sodra`
Grain: one row per `(run_month, jar_code, month)` for the selected Sodra run of each month. Rows with a null `jarCode` (about 5k rows: embassies and other insurers that have no company code) are **dropped**, because they can never match.

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
| run_month, run_id, data_month | passthrough |
| source_url, file_sha256 | passthrough: which official file this figure came from (traceability) |

Use `try_cast`. A value that is non-empty but fails to cast becomes null, and a test (`warn` severity) counts those rows so they are not silently lost.

Tests: `(run_month, jar_code, month)` unique; `jar_code` matches `^\d{9}$`.

`stg_places_errors` and `stg_run_log` are not built: nothing downstream needs them yet.

### 3.4 Seed: `category_evrk`
`dbt/seeds/category_evrk.csv` with columns `category,evrk_prefix`, one row per prefix in `config/categories.yaml`. dbt cannot read the YAML directly, so the seed duplicates it. A **pytest** test asserts that the seed matches `categories.yaml` exactly, which keeps one effective definition of "category".

---

## 4. Intermediate models (tables)

### 4.1 `int_page_company_codes`
Grain: one row per `(run_month, place_id, jar_code)` found on that place's fetched pages.

Extraction, from `stg_web_pages` where `is_fetched`:
1. Find every standalone 9-digit number: `\b\d{9}\b`, matched on the markdown text. `LT` + 9 digits (VAT) does not match because there is no word boundary between `T` and the digit. This is intended: VAT is not the company code.
2. For each hit, look at the 40 characters before it. The hit is **labelled** when that window matches `(?i)(įmonės|imones)\s+kodas|\bkodas\b|įm\.\s*k\.|company\s+code|juridinio asmens kodas`.
3. Keep only hits that exist in `stg_sodra.jar_code` for the Sodra run paired with that month (`int_run_pairs`), in any data month of that file. This validation removes phone numbers (`8 6xx xxxxx` style), order numbers and similar noise.

Columns: `run_month`, `place_id`, `jar_code`, `is_labelled` (true if any occurrence was labelled), `n_occurrences`, `found_on_roles` (`VARCHAR[]` of distinct `page_role`), `first_url` (the `final_url` of one page it was found on, preferring `legal_candidate`), `code_context`.

`code_context` is the evidence an analyst reads instead of opening the site: up to 40 characters before and 40 after one occurrence, with newlines collapsed to spaces. Use a labelled occurrence if there is one, preferring `legal_candidate` pages, then the earliest position. Example: `UAB „Virtuvės kava“, įmonės kodas 306238879, PVM kodas LT…`.

Also build `int_page_company_codes_unmatched` (table): the **labelled** hits that are **not** in `stg_sodra`, with the same columns. It is the list of "code shown on the site, but company not in this Sodra file", used for `match_status` and for diagnosing coverage.

Unit tests (dbt `unit_tests`), each a few hand-written markdown rows:
- `Įmonės kodas: 305693312` → one labelled hit.
- `**Įmonės kodas:** 305693312` (markdown bold) → labelled.
- `| Įmonės kodas | 121982951 |` (table cell) → labelled.
- `PVM kodas LT100012345678` and `LT123456789` → no hit.
- `Tel. 860012345` with no label, and not in the Sodra fixture → no hit.
- Same code on the home page and a legal page → one row, `n_occurrences = 2`, both roles.
- Two different valid codes on one place → two rows.
- `code_context` for `Įmonės kodas: 305693312` on a short page is that text with the label, and for a code on both a home and a legal page it comes from the legal page.
- The same place in two `run_month`s → rows in both months, independent of each other.

### 4.2 `int_places_matched` — the deliverable
Grain: **one row per `(run_month, place_id)`**: every place of each month's selected run, matched or not. Today there is one month with 171 rows.

Building it:
All steps below work within a `run_month`.

1. **Places → one row.** Aggregate `stg_places` by `(run_month, place_id)`: `categories` = sorted `VARCHAR[]` of its categories; every other place attribute from any row (they are identical across categories within a run).
2. **Crawl coverage.** From `stg_web_pages`: `crawl_status` =
   - `no_website` / `social_domain` / `robots_disallow` / `capped` / `error`, the home row's `skip_reason`, when the home page was not fetched;
   - `fetched` when the home page was fetched;
   - `not_crawled` when the place has no web page rows at all.
   
   Plus `pages_fetched` (count of fetched rows).
3. **Pick one code.** From `int_page_company_codes`, choose per place by: labelled first, then the most occurrences, then found on a `legal_candidate` page, then the lowest `jar_code` (for determinism). Record `n_candidate_codes` (the count of valid codes found) so ambiguous places stay visible.
4. **Attach Sodra.** Join the chosen `jar_code` to the company's **latest data month** in the Sodra run paired with this `run_month`.
5. **Confidence.** `match_confidence`, for matched rows only (null otherwise):
   - `high`: `code_is_labelled` and `evrk_matches_category` is true and `n_candidate_codes = 1`;
   - `low`: not `code_is_labelled` and `evrk_matches_category` is not true (false or null);
   - `medium`: every other matched row.

   A null `evrk_matches_category` on a matched row (the company's `evrk_code` is missing) counts as the EVRK check not passed, with reason `evrk_unknown` rather than `evrk_mismatch`.

   The lead step must only score `high` (and optionally `medium`) matches. A code on a site can belong to someone else (the web agency, a franchisor, a booking platform, a landlord), and the label plus activity-code checks are what tells them apart. `match_confidence_reasons` (`VARCHAR[]`) lists the failed checks (in this order: `no_label`, `evrk_mismatch`, `evrk_unknown`, `multiple_codes`), so the analyst sees why. It is `[]` for `high` and null when unmatched. A null `user_rating_count` counts as 0 in `chain_user_rating_count`.
6. **Chains.** Within a `run_month`, over matched rows: `places_sharing_code` = the number of places with the same `jar_code` (1 for a single-site company), and `chain_user_rating_count` = the sum of `user_rating_count` over those places. A company with six cafés and 40 employees should be compared on its total public activity, not flagged once per branch with one-sixth of its staff.

Columns:

| group | columns |
|---|---|
| place | `name`, `categories`, `primary_type`, `address`, `lat`, `lng`, `rating`, `user_rating_count`, `website_uri`, `business_status`, `google_maps_uri` |
| crawl | `crawl_status`, `pages_fetched` |
| key | `run_month`, `place_id` |
| match | `match_status` (`matched` / `code_not_in_sodra` / `no_code_found` / `not_fetched`), `match_confidence`, `match_confidence_reasons`, `jar_code`, `code_is_labelled`, `n_candidate_codes` |
| evidence | `code_source_url`, `code_context` |
| chain | `places_sharing_code`, `chain_user_rating_count` |
| company (latest month) | `company_name`, `municipality`, `evrk_code`, `evrk_name`, `sodra_month`, `num_insured`, `avg_wage`, `tax` |
| checks | `evrk_matches_category`, true when `evrk_code` starts with a prefix of any of the place's categories (via the `category_evrk` seed); null when unmatched |
| lineage | `places_run_id`, `sodra_run_id`, `sodra_source_url`, `sodra_file_sha256` |

`match_status` rules:
- `not_fetched` when `crawl_status <> 'fetched'`;
- else `matched` when a code was chosen;
- else `code_not_in_sodra` when the place has rows in `int_page_company_codes_unmatched`;
- else `no_code_found`.

Only `matched` rows have company, confidence and chain columns.

Tests:
- `(run_month, place_id)` unique and not null;
- row count per `run_month` equals the distinct `place_id` count in `stg_places` for that month (a custom singular test);
- `match_confidence` in (`high`, `medium`, `low`), not null exactly when `match_status = 'matched'`;
- `places_sharing_code >= 1` when matched;
- `code_context` contains `jar_code` when matched;
- `match_status` accepted values;
- `jar_code` is not null exactly when `match_status = 'matched'`;
- every matched `(sodra_run_id, jar_code)` exists in `stg_sodra`.

Unit tests for steps 5–6: one row per confidence level; a labelled code with an EVRK mismatch is `medium` with reason `evrk_mismatch`; two places with the same `jar_code` both get `places_sharing_code = 2` and the summed rating count; the same code in two different months is not counted as a chain.

Expected on the current data, as a sanity check rather than a test:
- 116 fetched;
- about 40–43 `matched`;
- 1 place with `n_candidate_codes > 1`;
- the split between `high` / `medium` / `low` is reported, not asserted, and recorded in the plan's verification step;
- 55 `not_fetched` (29 `no_website`, 20 `social_domain`, 4 `robots_disallow`, 2 `error`).

### 4.3 Not in this step
- Monthly Sodra history (trend over the 8 data months in a file) per matched company. `stg_sodra` keeps all months, so a later lead model can compute it directly.
- Month-over-month comparison models (new leads, dropped leads). The `run_month` grain makes them a plain self-join later.
- Collapsing chains into one lead. Places stay as separate rows with `places_sharing_code`. The lead step decides whether that is one lead or many.

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

## 7. Open questions to settle during implementation
1. Check that the DuckDB regex functions (RE2) support `\b`. If not, use `(^|[^0-9A-Za-z])(\d{9})($|[^0-9])` and extract group 2. Unit tests in §4.1 must pass either way.
2. dbt unit tests need a `dbt-core` version ≥ 1.8. Confirm the pinned version supports them on `dbt-duckdb`.
3. Check that the label window of 40 characters catches the table-cell layout (`| Įmonės kodas | 121982951`) on the real pages. If not, widen it and record the value used.
