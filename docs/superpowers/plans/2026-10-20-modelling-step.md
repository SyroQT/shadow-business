# Modelling Step (dbt) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A dbt project over `raw.*` that produces `intermediate.int_places_matched`: one row per `(run_month, place_id)`, with the Sodra company matched on the company code found on the place's crawled pages, plus match confidence, chain counts and evidence.

**Architecture:**
- **Layers:** staging views type and unpack each raw table, keeping one run per month per source. Intermediate tables pair runs, extract codes from markdown, then match.
- **Tests:** logic is pinned by dbt unit tests. A pytest harness builds a tiny raw DuckDB, runs `dbt build` against it, and checks end-to-end behaviour (run selection, months, fallbacks).
- **Pipeline:** `src/pipeline.py` runs `dbt build` after extraction.

**Tech Stack:** Python 3.13, uv, DuckDB 1.5.6, dbt-core ≥ 1.8 + dbt-duckdb, pytest.

**Spec:** `docs/specs/modelling-spec.md` (binding). Column lists, grains and rules are in the spec. This plan names the section instead of repeating them.

## Global Constraints

- Work on branch `modelling`. Commit at the end of each task. Messages are prefixed `MOD-NN:` and end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Never read, print or commit `.env`. No network in tests. dbt needs no keys.
- **No dbt packages** (ruling: avoids a networked `dbt deps` step and keeps Docker simple). Generic tests live in `dbt/tests/generic/`.
- dbt project in `dbt/`; `profiles.yml` in `dbt/`, committed. Profile `leads`, target `dev`, `type: duckdb`, `path: "{{ env_var('DB_PATH', 'data/leads.duckdb') }}"`.
- Schemas: `staging` (all `stg_*` as **views**) and `intermediate` (all `int_*` as **tables**). Configure the schema names so they are exactly `staging` / `intermediate`, without a target-name prefix: override `generate_schema_name`.
- `raw` is read-only for dbt.
- Every staging and intermediate model has `run_month` as the first column of its grain.
- Regex for codes: `\b\d{9}\b`. It is verified on DuckDB 1.5.6: it matches `123456789` and does not match inside `LT123456789` or `x860012345`. Label regex and window (40 characters before the code) exactly as in spec §4.1. On the real pages a window of 80 finds no more places than 40, so 40 stays.
- `.gitignore` gains `dbt/target/`, `dbt/logs/`, `dbt/dbt_packages/`.
- The full pytest suite (`uv run pytest -q`) stays green after every task. It is 194 passed, 2 skipped before this plan.

## Review Focus

1. **A second run in the same month:** only the newest usable run of that month is used; the older run's rows vanish from staging. Test: `test_latest_run_per_month_wins` (Task 2).
2. **A failed run:** a newer run whose places `run_log` row is `failed` is ignored, and the month falls back to the older usable run. Test: `test_failed_run_is_skipped` (Task 2).
3. **A places month with no Sodra at or before it:** the build does not crash. Every row has a null `jar_code`, and `match_status` is never `matched`. Test: `test_month_without_sodra` (Task 5).
4. **Places whose crawl never ran:** they still appear, with `crawl_status = 'not_crawled'` and `match_status = 'not_fetched'`. Test: `test_place_without_pages` (Task 5).
5. **Digit runs that are not a code:** an 18-digit number, a code at the very start or end of the markdown, and a null markdown. Unit tests `long_digit_run_no_hit`, `code_at_text_edges` and `null_markdown_no_rows` (Task 4).

---

## File Structure

```
dbt/
  dbt_project.yml
  profiles.yml
  macros/generate_schema_name.sql     # exact schema names
  macros/selected_runs.sql            # selected_runs(step) -> (run_month, run_id)
  seeds/category_evrk.csv
  seeds/_seeds.yml
  tests/generic/unique_combination.sql
  models/staging/_sources.yml
  models/staging/_staging.yml         # tests + docs for stg_*
  models/staging/stg_places.sql
  models/staging/stg_web_pages.sql
  models/staging/stg_sodra.sql
  models/intermediate/_intermediate.yml   # tests, docs, unit_tests
  models/intermediate/int_run_pairs.sql
  models/intermediate/int_page_code_hits.sql
  models/intermediate/int_page_company_codes.sql
  models/intermediate/int_page_company_codes_unmatched.sql
  models/intermediate/int_places_matched.sql
src/transform.py                      # run_dbt()
tests/dbt_harness.py                  # raw-row builders + run_dbt/query helpers
tests/test_dbt_project.py             # seed sync + empty-DB build
tests/test_dbt_staging.py
tests/test_dbt_matching.py
tests/test_pipeline.py
```

**Ruling:** `int_page_code_hits` (one row per code occurrence) is added beyond the spec's model list. It holds the per-occurrence context that both `int_page_company_codes` and `_unmatched` aggregate, so the regex runs in one place. It is a table, like all `int_*`.

---

### Task 1 (MOD-01): dbt scaffold, harness, seed

**Files:**
- Modify: `pyproject.toml`, `uv.lock`, `.gitignore`
- Create: `dbt/dbt_project.yml`, `dbt/profiles.yml`, `dbt/macros/generate_schema_name.sql`, `dbt/macros/selected_runs.sql`, `dbt/seeds/category_evrk.csv`, `dbt/seeds/_seeds.yml`, `dbt/tests/generic/unique_combination.sql`, `dbt/models/staging/_sources.yml`, `src/transform.py`, `tests/dbt_harness.py`, `tests/test_dbt_project.py`

**Interfaces:**
- Produces:
  - `src/transform.py`:
    - `DBT_DIR = Path(__file__).resolve().parent.parent / "dbt"`.
    - `run_dbt(args: list[str], db_path: Path) -> subprocess.CompletedProcess[str]`. It runs the `dbt` executable next to `sys.executable` (`Path(sys.executable).parent / "dbt"`) with `args + ["--project-dir", DBT_DIR, "--profiles-dir", DBT_DIR]`, env `DB_PATH=<absolute db_path>`, `capture_output=True, text=True`, and no `check`.
  - `tests/dbt_harness.py`:
    - `place(place_id, category, name, *, website=None, user_rating_count=None, rating=None, primary_type="cafe") -> dict`: a `raw.places` row whose `payload` has the keys seen in real data (`id, displayName{text}, primaryType, types, formattedAddress, location{latitude,longitude}, rating, userRatingCount, websiteUri, businessStatus, googleMapsUri`). Absent optionals are omitted from the payload.
    - `page(place_id, role, markdown, *, skip_reason=None, url="https://x.lt/") -> dict`: a `raw.web_pages` row.
    - `sodra(jar_code, *, month="202608", evrk="56.11.00", num_insured="3", avg_wage="1500.00", tax="900.00", name="UAB X") -> dict`: a `raw.sodra` row (all VARCHAR, other columns filled plausibly, `source_url="file:///s.zip"`, `file_sha256="abc"`).
    - `load(con, run: RunContext, *, places=(), pages=(), sodra=(), steps={"places": "ok"}) -> None`: inserts the rows with `src.db.insert_rows` and one `raw.run_log` row per `steps` item (`step -> status`).
    - `build(db_path) -> CompletedProcess` = `run_dbt(["build"], db_path)`; asserts returncode 0 and shows the dbt stdout in the assertion message.
    - `rows(db_path, sql) -> list[tuple]`: opens the DB read-only.
  - The `selected_runs(step)` macro returns SQL for a relation `(run_month, run_id)`: per `run_month`, the max `run_id` among `raw.run_log` rows with that `step` and `status <> 'failed'`.
  - The `unique_combination(model, columns)` generic test fails on any duplicate tuple.

- [ ] **Step 1: Write failing tests** in `tests/test_dbt_project.py`
  - `test_seed_matches_categories_yaml`: the set of `(category, evrk_prefix)` in `dbt/seeds/category_evrk.csv` equals the set from `config/categories.yaml` (`categories.*.evrk_prefixes`).
  - `test_build_on_empty_raw_db(tmp_path)`: `connect(tmp_path/"t.duckdb").close()` creates the empty raw schema, then `build(...)` succeeds and `select count(*) from staging.category_evrk` returns 5 (the YAML prefix count). Seeds are configured into schema `staging`.
- [ ] **Step 2:** Run `uv run pytest tests/test_dbt_project.py -q`. Expected: FAIL (no dbt).
- [ ] **Step 3: Implement.**
  - `uv add dbt-core dbt-duckdb` with no upper pins, and record the resolved versions in the commit message.
  - Confirm with `uv run dbt --version` that dbt-core is ≥ 1.8 (unit tests) and that it accepts DuckDB 1.5.6. If the resolver downgrades duckdb, stop and report: extraction is tested on 1.5.6.
  - Write the files above. `_sources.yml` declares the five raw tables and the source tests from spec §1.
- [ ] **Step 4:** Run `uv run pytest -q`. Expected: all pass. Also run `DB_PATH=data/leads.duckdb uv run dbt build --project-dir dbt --profiles-dir dbt` and confirm it exits 0 on the real DB (seed and source tests only).
- [ ] **Step 5: Commit** `MOD-01: dbt project scaffold, harness, category_evrk seed`.

---

### Task 2 (MOD-02): `stg_places`, `stg_web_pages`

**Files:**
- Create: `dbt/models/staging/stg_places.sql`, `dbt/models/staging/stg_web_pages.sql`, `dbt/models/staging/_staging.yml`, `tests/test_dbt_staging.py`

**Interfaces:**
- Consumes: `selected_runs('places')`, the harness, `unique_combination`.
- Produces: `staging.stg_places` and `staging.stg_web_pages` with exactly the columns in spec §3.1 and §3.2. Web pages join `selected_runs('places')` on `run_id`.

- [ ] **Step 1: Write failing tests** in `tests/test_dbt_staging.py`. Runs come from `new_run("2026-10", now=datetime(2026,10,1,tzinfo=UTC))` and similar.
  - `test_places_unpacked`: one place with every payload key. `stg_places` returns the right `name`, `types` (a list), `lat`, `lng`, `user_rating_count` (an int), `website_uri` and `run_month = '2026-10'`. A second place with no `websiteUri`/`rating` gets nulls.
  - `test_latest_run_per_month_wins`: two `ok` runs in `2026-10` (01T00 and 02T00) with different places. Only the later run's places are in `stg_places`.
  - `test_failed_run_is_skipped`: an `ok` run on 01T00 and a `failed` run on 02T00. Only the 01T00 places.
  - `test_months_kept_side_by_side`: runs in `2026-09` and `2026-10`. Both months are in `stg_places`, and each has its own rows.
  - `test_place_in_two_categories_kept`: the same `place_id` under two categories gives two rows.
  - `test_web_pages_follow_places_run`: pages from the selected run only. `is_fetched` is true for a page with markdown and no skip reason, and false for `no_website`.
- [ ] **Step 2:** Run `uv run pytest tests/test_dbt_staging.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement** both models and their `_staging.yml` tests (spec §3.1, §3.2):
  - `unique_combination(run_month, place_id, category)`;
  - not-null tests;
  - a `relationships` test from `stg_places.category` to `ref('category_evrk').category`;
  - accepted values on `page_role` and `skip_reason`;
  - `stg_web_pages (run_month, place_id)` exists in `stg_places`, as a singular test in `dbt/tests/`.

  Places dedupe: keep one row per `(run_month, place_id, category)` by earliest `retrieved_at`.
- [ ] **Step 4:** Run `uv run pytest -q`, which must pass. Then build on the real DB: `stg_places` has 171 rows for `2026-10`, and `stg_web_pages` has 344 rows (171 home + 173 legal_candidate).
- [ ] **Step 5: Commit** `MOD-02: stg_places and stg_web_pages with monthly run selection`.

---

### Task 3 (MOD-03): `stg_sodra`

**Files:**
- Create: `dbt/models/staging/stg_sodra.sql`
- Modify: `dbt/models/staging/_staging.yml`, `tests/test_dbt_staging.py`

**Interfaces:**
- Consumes: `selected_runs('sodra')`.
- Produces: `staging.stg_sodra` with the columns of spec §3.3, including `run_month`, `source_url` and `file_sha256`.

- [ ] **Step 1: Write failing tests:**
  - `test_sodra_typed`: `month '202608'` becomes `date '2026-08-01'`; `'2754.09'` becomes `Decimal('2754.09')`; `num_insured` `'4'` becomes `4`; `''` becomes `None`; `jar_code` `'012345678'` keeps its leading zero.
  - `test_sodra_null_jar_dropped`: a row with `jarCode` None is absent.
  - `test_sodra_bad_number_warns_not_fails`: `avgWage = 'abc'`. The build succeeds, `avg_wage` is null, and dbt stdout reports a `WARN` for the cast-failure test.
- [ ] **Step 2:** Run the tests. Expected: FAIL.
- [ ] **Step 3: Implement.** Empty strings become null via `nullif(trim(x), '')`, then `try_cast`. Tests:
  - `unique_combination(run_month, jar_code, month)`;
  - a `jar_code` regex test `^\d{9}$`, as a singular test or a generic `matches_regex` in `tests/generic/`;
  - a cast-failure singular test with `config(severity='warn')`, counting rows where the raw value is non-empty but the typed value is null.
- [ ] **Step 4:** Run `uv run pytest -q`. On the real DB: 835,029 rows (840,194 minus 5,165 null `jarCode`), and `(run_month, jar_code, month)` is unique.
- [ ] **Step 5: Commit** `MOD-03: stg_sodra typed, null jar codes dropped`.

---

### Task 4 (MOD-04): run pairing and code extraction

**Files:**
- Create:
  - `dbt/models/intermediate/int_run_pairs.sql`
  - `dbt/models/intermediate/int_page_code_hits.sql`
  - `dbt/models/intermediate/int_page_company_codes.sql`
  - `dbt/models/intermediate/int_page_company_codes_unmatched.sql`
  - `dbt/models/intermediate/_intermediate.yml`
  - `tests/test_dbt_matching.py`

**Interfaces:**
- Consumes: `stg_web_pages`, `stg_sodra`, `selected_runs`.
- Produces:
  - `int_run_pairs(run_month, places_run_id, sodra_run_id)`: one row per places month. `sodra_run_id` is the Sodra run of the same month, else the latest earlier month's, else null (spec §2).
  - `int_page_code_hits(run_month, place_id, page_role, final_url, hit_index, jar_code, before_text, after_text, is_labelled, in_sodra)`: one row per occurrence. `in_sodra` checks `stg_sodra` rows of the paired `sodra_run_id`.
  - `int_page_company_codes` and `int_page_company_codes_unmatched`: columns as in spec §4.1, including `code_context`.

**Occurrence algorithm.** The test cases don't fully pin this down, so it is fixed here. A single `regexp_extract_all` with a context group would swallow an earlier code inside a later code's window, so the occurrences are split instead:
```sql
-- per fetched page
codes := regexp_extract_all(markdown, '\b\d{9}\b')
segs  := regexp_split_to_array(markdown, '\b\d{9}\b')   -- len(segs) = len(codes) + 1
-- for i in 1..len(codes):
jar_code    := codes[i]
before_text := right(segs[i], 40)
after_text  := left(segs[i + 1], 40)
is_labelled := regexp_matches(before_text, <label regex from spec §4.1>)
code_context := trim(regexp_replace(before_text || jar_code || after_text, '\s+', ' ', 'g'))
```
Unnest `i` with `generate_series(1, len(codes))`. Pages with a null markdown, or no codes, produce no rows. Aggregation into `int_page_company_codes` follows spec §4.1:
- `code_context` and `first_url` come from the preferred occurrence: labelled first, then `legal_candidate`, then the lowest `hit_index`;
- `n_occurrences` = the count of occurrences;
- `found_on_roles` = a sorted distinct list.

`_unmatched` uses the same columns, keeps only rows with `is_labelled and not in_sodra`, and its `is_labelled` is always true.

- [ ] **Step 1: Write the dbt unit tests** in `_intermediate.yml`, on `int_page_code_hits` and `int_page_company_codes`. Mock `stg_web_pages`, `stg_sodra` and `int_run_pairs` as inputs. Cases are those in spec §4.1, plus Review Focus 5:
  - `labelled_plain` (`Įmonės kodas: 305693312`);
  - `labelled_bold` (`**Įmonės kodas:** 305693312`);
  - `labelled_table_cell` (`| Įmonės kodas | 121982951 |`);
  - `vat_no_hit` (`PVM kodas LT100012345678`, `LT123456789`);
  - `phone_not_in_sodra_no_row` (`Tel. 860012345`, absent from the mocked Sodra rows);
  - `same_code_two_pages` (one row, `n_occurrences = 2`, `found_on_roles = ['home','legal_candidate']`, `code_context` from the legal page);
  - `two_codes_one_place` (two rows);
  - `long_digit_run_no_hit` (`123456789012345678`);
  - `code_at_text_edges` (markdown exactly `305693312`, and `kodas 305693312` at the end);
  - `null_markdown_no_rows`;
  - `unmatched_labelled_code` (labelled, not in Sodra: in `_unmatched` and not in `int_page_company_codes`).

  Add pytest `test_run_pairs_fallback` in `tests/test_dbt_matching.py`, with places months 2026-09 and 2026-10 and a Sodra run only in 2026-09. Both pairs point to the 2026-09 Sodra run. Add `test_run_pairs_no_sodra`: a places month with no Sodra at or before it gets `sodra_run_id` null.
- [ ] **Step 2:** Run `uv run dbt test --select test_type:unit ...` through the harness, and the pytest tests. Expected: FAIL.
- [ ] **Step 3: Implement** the four models. Schema tests:
  - `unique_combination(run_month, place_id, jar_code)` on both code tables;
  - `jar_code` regex;
  - a singular test that `code_context` contains `jar_code`.
- [ ] **Step 4:** Run `uv run pytest -q` and `dbt build`, both green. On the real DB: `int_page_company_codes` covers 43 distinct places; 40 places have at least one labelled code; exactly 1 place has 2 or more codes. If the counts differ, report the actual numbers and the reason. Don't tune the regex to hit them.
- [ ] **Step 5: Commit** `MOD-04: run pairing and company-code extraction from crawled pages`.

---

### Task 5 (MOD-05): `int_places_matched`: grain, crawl status, code choice, Sodra

**Files:**
- Create: `dbt/models/intermediate/int_places_matched.sql`
- Modify: `dbt/models/intermediate/_intermediate.yml`, `tests/test_dbt_matching.py`

**Interfaces:**
- Consumes: `stg_places`, `stg_web_pages`, `stg_sodra`, `int_run_pairs`, `int_page_company_codes`, `int_page_company_codes_unmatched`, and the `category_evrk` seed.
- Produces: `intermediate.int_places_matched` with every column in spec §4.2 **except** `match_confidence`, `match_confidence_reasons`, `places_sharing_code` and `chain_user_rating_count`, which Task 6 adds. Steps 1–4 of spec §4.2 and the `match_status` rules.

- [ ] **Step 1: Write failing tests.** Unit tests in `_intermediate.yml`:
  - `pick_labelled_over_frequent`: an unlabelled code with 3 occurrences against a labelled one with 1. The labelled code wins, `n_candidate_codes = 2`.
  - `pick_tiebreak_lowest_code`.
  - `status_code_not_in_sodra`.
  - `status_no_code_found`.
  - `status_not_fetched_social`: `crawl_status = 'social_domain'`.
  - `latest_sodra_month_attached`: the company has rows for `2026-07-01` and `2026-08-01`, and `sodra_month = 2026-08-01` with August's `num_insured`.
  - `evrk_check`: `56.11.00` for `cafes_restaurants` gives true; `47.11.00` gives false; unmatched gives null.
  - `two_categories_one_row`: `categories = ['cafes_restaurants','hair_beauty']`.

  Pytest, end to end through the harness: `test_place_without_pages` and `test_month_without_sodra` (Review Focus 3 and 4). `test_two_months_independent`: the same place in 2026-09 (no code page) and 2026-10 (with a code page). It has `no_code_found` in September and `matched` in October.
- [ ] **Step 2:** Run the tests. Expected: FAIL.
- [ ] **Step 3: Implement.** Schema tests:
  - `unique_combination(run_month, place_id)`;
  - accepted values on `match_status` and `crawl_status` (`fetched`, `not_crawled`, and the 5 skip reasons);
  - a singular test that per-`run_month` row counts equal the distinct places in `stg_places`;
  - a singular test that `jar_code is not null` ⇔ `match_status = 'matched'`;
  - a singular test that matched `(sodra_run_id, jar_code)` exists in `stg_sodra`.
- [ ] **Step 4:** Run `uv run pytest -q` and `dbt build`, both green. On the real DB:
  - 171 rows;
  - `crawl_status`: 116 fetched, 29 no_website, 20 social_domain, 4 robots_disallow, 2 error;
  - `match_status = 'matched'` is 40–43 (report the exact number).
- [ ] **Step 5: Commit** `MOD-05: int_places_matched with crawl status, code choice and Sodra attach`.

---

### Task 6 (MOD-06): match confidence and chain counts

**Files:**
- Modify: `dbt/models/intermediate/int_places_matched.sql`, `dbt/models/intermediate/_intermediate.yml`

**Interfaces:**
- Produces: `match_confidence`, `match_confidence_reasons`, `places_sharing_code` and `chain_user_rating_count` on `int_places_matched`, with the rules of spec §4.2 steps 5–6.
  - The reasons list is ordered `no_label`, `evrk_mismatch`, `multiple_codes`; it is empty (`[]`) for `high` and null when unmatched.
  - A null `user_rating_count` counts as 0 in the chain sum.

- [ ] **Step 1: Write failing unit tests:**
  - `confidence_high` (labelled, EVRK fits, 1 code).
  - `confidence_medium_evrk_mismatch` (reasons `['evrk_mismatch']`).
  - `confidence_medium_no_label`.
  - `confidence_medium_multiple_codes` (both checks true, 2 codes, reasons `['multiple_codes']`).
  - `confidence_low` (no label, EVRK mismatch).
  - `confidence_null_when_unmatched`.
  - `chain_two_places`: two places, same `jar_code`, with 120 and null reviews. Both get `places_sharing_code = 2` and `chain_user_rating_count = 120`.
  - `chain_not_across_months`: the same code in 2026-09 and 2026-10 gives 1 in each.
- [ ] **Step 2:** Run the tests. Expected: FAIL.
- [ ] **Step 3: Implement.** Schema tests:
  - accepted values on `match_confidence`;
  - a singular test that `match_confidence is not null` ⇔ matched;
  - a singular test that `places_sharing_code >= 1` when matched.
- [ ] **Step 4:** Run `uv run pytest -q` and `dbt build`, both green. On the real DB, report the `high`/`medium`/`low` split and the number of places with `places_sharing_code > 1`. Both are reported, not asserted.
- [ ] **Step 5: Commit** `MOD-06: match confidence and chain counts`.

---

### Task 7 (MOD-07): pipeline wiring and README

**Files:**
- Modify: `src/pipeline.py`, `README.md`
- Create: `tests/test_pipeline.py`

**Interfaces:**
- Consumes: `src.transform.run_dbt`, `src.extract.__main__.main`, `src.settings.load_settings_or_exit` (for `db_path`).
- Produces: `src/pipeline.py::main() -> int`.
  - It runs `extract all`. If that returns non-zero, it logs `step="dbt"` skipped and returns that code.
  - Otherwise it runs `run_dbt(["build"], settings.db_path)`, logs `step="dbt"` with the status and duration (not dbt's stdout, which goes to stderr only when dbt fails), and returns 0 or 1.
  - Export stays a `not_implemented` log line.

- [ ] **Step 1: Write failing tests** in `tests/test_pipeline.py`, monkeypatching `extract_main` and `run_dbt`:
  - `test_dbt_runs_after_successful_extract`: `run_dbt` is called once with `["build"]`, and the result is 0.
  - `test_dbt_skipped_when_extract_fails`: `extract_main` returns 1, `run_dbt` is not called, and the result is 1.
  - `test_dbt_failure_fails_pipeline`: `run_dbt` returns code 1, and the result is 1.
- [ ] **Step 2:** Run the tests. Expected: FAIL.
- [ ] **Step 3: Implement.** README "Modelling" section:
  - the two commands from spec §5 (minus `deps`);
  - one line per model;
  - the one-writer warning;
  - this query, with its output on the real DB pasted below it:

    ```sql
    select run_month, match_status, match_confidence, count(*)
    from intermediate.int_places_matched
    group by all
    order by all;
    ```
- [ ] **Step 4:** Run `uv run pytest -q`, which must pass, then a final `dbt build` on the real DB, which must exit 0. Don't run `src/pipeline.py` itself: it calls `extract all`, which hits live APIs.
- [ ] **Step 5: Commit** `MOD-07: run dbt build from the pipeline; README modelling section`.
