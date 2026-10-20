# Leads Core (lead list + export) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** From `intermediate.int_places_matched`, build `marts.mart_leads` (one row per company per run month, with tier, ranks, reason text and last month's tier) and an export that writes `output/<run_month>/leads.csv` and `leads.md`, with `--top N` for demo output.

**Architecture:**
- **Models:** `int_company_month` rolls places up to companies. `int_lead_signals` applies eligibility and computes the two signals. `mart_leads` adds tier, ranks, reason text and `prev_tier`. Signal and tier rules are dbt macros, so the validation plan can reuse them.
- **Tests:** logic is pinned by dbt unit tests in YAML. One pytest harness test builds a small raw DB end to end and drives the export.
- **Pipeline:** `src/pipeline.py` calls the export after a successful `dbt build`.

**Tech Stack:** Python 3.13, uv, DuckDB 1.5.6, dbt-core 1.12 + dbt-duckdb 1.11, pytest.

**Spec:** `docs/specs/leads-spec.md` (binding): §0–§7, §9 (minus "Validation tests"), §10. **§8 (validation) is the second plan, `2026-10-20-leads-validation.md`**: this plan leaves out `match_rejected`, `match_review_*`, `settings_as_lead` / `settings_total` and the sensitivity / precision sections of `leads.md`. Column lists and rules are in the spec; this plan names the section instead of repeating them.

## Global Constraints

- Branch `leads`. Commit at the end of each task. Messages are prefixed `LEAD-NN:` and end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Never read, print or commit `.env`. No network in tests. Never run `src/pipeline.py` for real (it hits live APIs); test it with monkeypatching as `tests/test_pipeline.py` does.
- No dbt packages. Reuse `unique_combination` and `matches_regex` from `dbt/tests/generic/`.
- Schemas: new `marts` (tables), set in `dbt/dbt_project.yml` like `staging` / `intermediate`. New `int_*` models are tables in `intermediate`.
- All thresholds are dbt vars in `dbt_project.yml` (spec §3): `min_monthly_wage: 1153`, `low_wage_margin: 0.10`, `busy_multiple: 2.0`, `min_peers: 5`. Never a literal in SQL.
- Unit tests live next to the models (`_intermediate.yml`, new `dbt/models/marts/_marts.yml`), dict row format as in the existing `_intermediate.yml`.
- `uv run pytest -q` stays green after every task. Before this plan it is **217 passed, 2 skipped**.
- `output/` is git-ignored in this plan (the committed example output is a later task).

## Rulings (made while planning)

1. **Category label.** The seed `category_lead_rules` gets a 4th column `label` (`cafés and restaurants`, `hair and beauty`, `car service`), used in reason text and cards instead of the key. Reason text is for the analyst; `cafes_restaurants` is not. Cost if wrong: one column to drop.
2. **Number format** (spec §12 q2). Counts plain integers (`1006`), money `€` + two decimals with no thousands separator (`€989.55`), multiples one decimal + `×` (`2.1×`). The same strings in SQL and in Python (Python reads `lead_reason` from the table and formats card numbers with the same rules).
3. **Wage wording.** `below` when `avg_wage < min_monthly_wage`, `at` when equal, `near` when within the margin above.
4. **`prev_tier`.** The tier in the latest `run_month` earlier than this one that exists in `mart_leads` at all; null if the company is not in that month. A company absent last month but present two months ago gets null.
5. **Median.** DuckDB `median()` (average of the two middle values for an even count), as a window over `(run_month, lead_category)` on eligible rows. If a window `median` fails on the pinned version, compute it in a grouped CTE and join back (spec §12 q1).
6. **Cards** in `leads.md` are fenced ` ```text ` blocks, so the column alignment of spec §7 survives rendering.
7. **`--top` source.** `Settings.leads_top_n: int = 10` (env `LEADS_TOP_N`). `python -m src.export --top N` overrides it.

## Review Focus

1. **A database with no matched companies** (fresh DB, or a month where nothing matched): `dbt build` passes, `mart_leads` is empty, and the export logs it and writes nothing instead of crashing. Tests: `test_build_on_empty_raw_db` keeps passing (Task 3), `test_export_no_rows` (Task 4).
2. **A month where every company is excluded:** every `priority_rank` is null, `--top 10` writes no cards, the summary table still lists the excluded counts. Unit test `all_excluded_no_priority` (Task 3), `test_export_only_excluded` (Task 4).
3. **Older months in the table:** export uses the latest `run_month` only, and `prev_tier` on that month points at the older one. Test `test_export_latest_month_only` (Task 4), unit test `prev_tier_latest_earlier_month` (Task 3).
4. **Exporting twice:** a second export of the same month overwrites both files with identical content. Test `test_export_idempotent` (Task 4).
5. **A company with 0 insured employees:** stays eligible, ratio uses 1, reason says "0 insured employees". Unit test `zero_insured_uses_one` (Task 2) and `reason_zero_insured` (Task 3).

---

## File Structure

```
dbt/
  dbt_project.yml                         # + marts schema, + vars
  macros/lead_signals.sql                 # lead_is_busy, lead_is_low_wage, lead_tier
  seeds/category_lead_rules.csv           # category, min_reviews, caveat, label
  seeds/_seeds.yml                        # + category_lead_rules
  models/intermediate/int_company_month.sql
  models/intermediate/int_lead_signals.sql
  models/intermediate/_intermediate.yml   # + tests, unit tests
  models/marts/mart_leads.sql
  models/marts/_marts.yml
  tests/int_company_month_row_counts.sql
  tests/mart_leads_flag_has_both_signals.sql
  tests/category_lead_rules_complete.sql
src/export.py                             # replace the stub
src/settings.py                           # + leads_top_n
src/pipeline.py                           # call export
tests/test_dbt_leads.py                   # harness end-to-end
tests/test_export.py
tests/test_pipeline.py                    # + export cases
README.md                                 # Leads section
.gitignore                                # + output/
```

---

### Task 1 (LEAD-01): `int_company_month`

**Files:**
- Create: `dbt/models/intermediate/int_company_month.sql`, `dbt/tests/int_company_month_row_counts.sql`
- Modify: `dbt/models/intermediate/_intermediate.yml`

**Interfaces:**
- Consumes: `int_places_matched` (modelling spec §4.2 columns).
- Produces: `intermediate.int_company_month`, grain `(run_month, jar_code)`, exactly the columns of spec §1. `places` is `STRUCT(place_id VARCHAR, name VARCHAR, address VARCHAR, user_rating_count INTEGER, google_maps_uri VARCHAR)[]`. `best_match_confidence_reasons` is `VARCHAR[]`.

- [ ] **Step 1: Write failing unit tests** in `_intermediate.yml` (model `int_company_month`, input `ref('int_places_matched')`):
  - `company_two_places_rolled_up`: two matched places with `jar_code "111111111"`, review counts 100 and 300 → one row, `n_places: 2`, `chain_user_rating_count: 400`, `places[1].place_id` is the 300-review place.
  - `company_best_confidence`: places with `medium` (`['no_label']`) and `high` (`[]`) → `best_match_confidence: high`, `best_match_confidence_reasons: []`, `code_source_url` / `code_context` from the high place.
  - `company_confidence_tie_lowest_place`: two `high` places `p2`, `p1` → evidence from `p1`.
  - `lead_category_majority`: places with `categories` `[hair_beauty]`, `[hair_beauty]`, `[cafes_restaurants]` → `hair_beauty`.
  - `lead_category_tie`: places with `[cafes_restaurants]`, `[cafes_restaurants, hair_beauty]`, `[hair_beauty, car_service]` (cafés 2, hair 2, car 1) → `cafes_restaurants` (alphabetically first).
  - `lead_category_single`: one place `[hair_beauty]` → `hair_beauty`.
  - `any_operational`: one `OPERATIONAL` and one `CLOSED_TEMPORARILY` place → true; a company with only `CLOSED_PERMANENTLY` → false.
  - `unmatched_rows_ignored`: a `no_code_found` row produces no company row.
  - `months_separate`: same `jar_code` in `2026-09` and `2026-10` → two rows.
- [ ] **Step 2:** Run `uv run dbt build --project-dir dbt --profiles-dir dbt --select int_company_month` (real DB, as in the modelling plan; dbt only writes the `staging` / `intermediate` / `marts` schemas, never `raw`). Expected: FAIL (model missing).
- [ ] **Step 3: Implement** `int_company_month.sql`, plus schema tests: `unique_combination(run_month, jar_code)`, `not_null` on key, `best_match_confidence` accepted values `high`/`medium`/`low`, `n_places >= 1`. Singular test `int_company_month_row_counts.sql`: per `run_month`, row count = `count(distinct jar_code)` of matched rows in `int_places_matched` (spec §9).
- [ ] **Step 4:** `uv run pytest -q` (all pass) and the real-DB build: 43 matched places → expect about 25 company rows for `2026-10` (record the number).
- [ ] **Step 5: Commit** `LEAD-01: int_company_month (company grain, best confidence, places list)`.

---

### Task 2 (LEAD-02): eligibility, signals, macros, seed, vars

**Files:**
- Create: `dbt/seeds/category_lead_rules.csv`, `dbt/macros/lead_signals.sql`, `dbt/models/intermediate/int_lead_signals.sql`, `dbt/tests/category_lead_rules_complete.sql`
- Modify: `dbt/dbt_project.yml` (vars), `dbt/seeds/_seeds.yml`, `dbt/models/intermediate/_intermediate.yml`, `tests/test_dbt_project.py`

**Interfaces:**
- Consumes: `int_company_month`.
- Produces:
  - Seed `category_lead_rules(category, min_reviews INTEGER, caveat, label)` with the three rows of spec §3 plus Ruling 1's labels.
  - Macros in `lead_signals.sql`, each returning a SQL boolean / varchar expression:
    - `lead_is_busy(rpe_vs_median, peer_count, busy_multiple, min_peers)` → null when `peer_count < min_peers`, else `rpe_vs_median >= busy_multiple`;
    - `lead_is_low_wage(avg_wage, min_monthly_wage, low_wage_margin)` → null when `avg_wage` is null, else `avg_wage <= min_monthly_wage * (1 + low_wage_margin)`;
    - `lead_tier(exclusion_reason, is_busy, is_low_wage)` → `'excluded'` / `'flag'` / `'watch'` / `'clear'` per spec §4.
  - `intermediate.int_lead_signals`: every `int_company_month` column, plus `exclusion_reason` (spec §2 checks 2–5, in order: `weak_match`, `not_operational`, `no_employee_data`, `too_few_reviews`), `min_reviews`, `category_caveat`, `category_label`, and the spec §4 signal columns (`reviews_per_employee`, `peer_count`, `category_median_rpe`, `rpe_vs_median`, `is_busy`, `is_low_wage`, `wage_vs_minimum`), null when excluded.

- [ ] **Step 1: Write failing tests.**
  - `tests/test_dbt_project.py::test_lead_rules_seed_matches_categories_yaml`: the `category` set in `category_lead_rules.csv` equals the keys of `config/categories.yaml` `categories`.
  - Unit tests in `_intermediate.yml` (model `int_lead_signals`; inputs `ref('int_company_month')`, `ref('category_lead_rules')`):
    - `eligible_high`: `high`, `OPERATIONAL`, 400 reviews, 10 insured, cafés → `exclusion_reason: null`, `reviews_per_employee: 40.0`.
    - `medium_no_label_eligible` / `medium_two_reasons_weak`: `['no_label']` → null; `['no_label','evrk_mismatch']` → `weak_match`.
    - `exclusion_order`: a `low` match with 10 reviews → `weak_match` (not `too_few_reviews`).
    - one test each for `not_operational`, `no_employee_data` (`num_insured: null`), `too_few_reviews` (car service 49 reviews → excluded; 50 → eligible).
    - `zero_insured_uses_one`: `num_insured: 0`, 300 reviews → eligible, `reviews_per_employee: 300.0`.
    - `peer_median`: 5 eligible cafés with rpe 10, 20, 30, 40, 100 → `category_median_rpe: 30`, the 100 row `rpe_vs_median: 3.3`, `is_busy: true`; the 40 row `is_busy: false`; an excluded café in the same month is not a peer (`peer_count: 5`).
    - `too_few_peers`: 4 eligible hair salons → `is_busy: null`, `rpe_vs_median` not null.
    - `wage_boundary`: `avg_wage` 1268.30 → `is_low_wage: true`; 1268.31 → false; null → null; `wage_vs_minimum` for 989.55 is `0.86`.
  - Use dbt unit-test `overrides: {vars: {...}}` only if a test needs non-default thresholds; the defaults above need none.
- [ ] **Step 2:** Run the pytest test and the unit tests (`dbt build --select int_lead_signals`). Expected: FAIL.
- [ ] **Step 3: Implement** the seed (and `_seeds.yml` column types: `min_reviews` integer), the vars, the macros, `int_lead_signals.sql` (macros called with `var(...)`), schema tests (`unique_combination(run_month, jar_code)`, `exclusion_reason` accepted values = the four above), and singular test `category_lead_rules_complete.sql`: every `lead_category` in `int_company_month` has a seed row.
- [ ] **Step 4:** `uv run pytest -q` (all pass); real-DB build. Expected near spec §10: about 20 eligible (14 cafés, 4 car service, 2 hair). Record the numbers.
- [ ] **Step 5: Commit** `LEAD-02: eligibility, busy and low-wage signals, lead macros and thresholds`.

---

### Task 3 (LEAD-03): `mart_leads`

**Files:**
- Create: `dbt/models/marts/mart_leads.sql`, `dbt/models/marts/_marts.yml`, `dbt/tests/mart_leads_flag_has_both_signals.sql`, `tests/test_dbt_leads.py`
- Modify: `dbt/dbt_project.yml` (marts schema)

**Interfaces:**
- Consumes: `int_lead_signals`, the `lead_tier` macro.
- Produces: `marts.mart_leads`, grain `(run_month, jar_code)`, columns of spec §6 **without** the `match_review_*` and robustness rows (added by the validation plan), in the spec's group order. `thresholds used` = `min_monthly_wage`, `busy_multiple` as numbers.

- [ ] **Step 1: Write failing tests.**
  - Unit tests in `_marts.yml` (model `mart_leads`, input `ref('int_lead_signals')`):
    - `tier_each`: four companies → `flag`, `watch` (normal wage), `watch` (`is_low_wage: null`), `clear`, plus one `excluded`.
    - `lead_rank_within_tier` and `priority_rank_order`: a `watch` with `rpe_vs_median 2.1` ranks above a `clear` with `2.5`; excluded `priority_rank: null`; ties broken by `jar_code`.
    - `all_excluded_no_priority`: a month of only excluded rows → every `priority_rank` null.
    - `reason_flag`: exact string for 1006 reviews, 1 place, 4 insured, rpe 251.5, 6.3× the cafés and restaurants median (40.0), wage 989.55:
      `"1006 Google reviews across 1 place(s) and 4 insured employees: 251.5 reviews per employee, 6.3× the cafés and restaurants median (40.0). Average wage €989.55, below the minimum wage (€1153)."`
    - `reason_watch_unknown_wage`, `reason_clear_below`, `reason_clear_few_peers` (`"Only 4 comparable companies in hair and beauty; at least 5 needed to compare."`), `reason_zero_insured` (`"... and 0 insured employees: ..."`), and one excluded sentence per reason (fixed strings chosen by the implementer, asserted exactly).
    - `prev_tier_latest_earlier_month`: company in `2026-08` (`clear`) and `2026-10` (`watch`), month list `2026-08, 2026-09, 2026-10` where it is absent in `2026-09` → `2026-10` row `prev_tier: null`; a company in `2026-09` and `2026-10` → `prev_tier` = its `2026-09` tier.
  - `tests/test_dbt_leads.py::test_end_to_end_tiers(tmp_path)`: with `tests/dbt_harness.py`, load one run with 7 cafés, each with a fetched home page `"Įmonės kodas: <code>"` and a Sodra row (EVRK `56.11.00`, 10 insured): reviews 300, 300, 300, 300, 2000 (wage `1000.00`), 1500 (wage `2000.00`), and one with 50 reviews. Build, then assert `mart_leads` tiers: 2000 → `flag`, 1500 → `watch`, four `clear`, 50 → `excluded` / `too_few_reviews`. Expose the loader as `tests/dbt_harness.py::load_lead_demo(con, run)` for Task 4.
- [ ] **Step 2:** Run both. Expected: FAIL.
- [ ] **Step 3: Implement** `mart_leads.sql`, the `marts` schema config, schema tests (spec §9 schema list: unique key, `tier` accepted values, `exclusion_reason` not null exactly when excluded, `lead_reason` not null), and singular test `mart_leads_flag_has_both_signals.sql`.
- [ ] **Step 4:** `uv run pytest -q` (all pass); real-DB build. Expected per spec §10: 0 `flag`, about 2 `watch`. Record the actual tier split per category.
- [ ] **Step 5: Commit** `LEAD-03: mart_leads with tiers, ranks, reason text and prev_tier`.

---

### Task 4 (LEAD-04): export and pipeline

**Files:**
- Modify: `src/export.py` (replace the stub), `src/settings.py`, `src/pipeline.py`, `tests/test_pipeline.py`, `.gitignore`
- Create: `tests/test_export.py`

**Interfaces:**
- Consumes: `marts.mart_leads`; `tests/dbt_harness.py::load_lead_demo`, `build`.
- Produces:
  - `src/export.py`:
    - `export_leads(db_path: Path, out_dir: Path, top_n: int) -> Path | None`: opens the DB read-only, picks `max(run_month)` in `marts.mart_leads`, writes `out_dir/<run_month>/leads.csv` and `leads.md`, returns that directory. Returns `None` (and logs `event="export", status="no_rows"`) when the table is empty.
    - `main(argv: list[str] | None = None) -> int`: `--top N` (default `Settings.leads_top_n`), `--out` (default `output`); DB from `load_settings_or_exit().db_path`. `python -m src.export` runs it.
  - `Settings.leads_top_n: int = 10`.
  - `pipeline.main()` calls `export_leads(settings.db_path, Path("output"), settings.leads_top_n)` after a successful dbt build and logs `step="export"` with `status` and `path`; an exception in export returns 1.

- [ ] **Step 1: Write failing tests** in `tests/test_export.py`. A module-scoped fixture builds one tmp DB with `load_lead_demo` + `build` (one dbt build for the whole module).
  - `test_export_writes_both_files`: both files exist under `<out>/2026-10/`.
  - `test_csv_has_every_row`: CSV row count = `mart_leads` row count; `places` cell is `"<name> (<google_maps_uri>); ..."`; header = mart columns in table order.
  - `test_top_zero_flag_and_watch_only`: `top_n=0` → exactly one `FLAG #1` and one `WATCH #1` card; no `CLEAR`; no "Closest to the threshold" heading.
  - `test_top_n_adds_clear_cards`: `top_n=4` → flag + watch cards, then the heading and `CLEAR` cards for `priority_rank` 3 and 4 only; no `EXCLUDED` card ever.
  - `test_csv_same_with_and_without_top`: CSV bytes identical for `top_n=0` and `top_n=10`.
  - `test_summary_table`: `leads.md` starts with a table of counts per category × tier including `excluded`.
  - `test_card_lines`: the flag card contains `Busy:`, `Paid:`, `Match:`, the code context in quotes, one `Places:` line per place, `Note:` with the caveat, `Last month: not present`.
  - `test_export_idempotent`: two exports → identical bytes.
  - `test_export_latest_month_only`: a second run in `2026-11` loaded and built → output dir `2026-11` only, cards show `Last month: <2026-10 tier>`.
  - `test_export_no_rows(tmp_path)`: an empty built DB (as `test_build_on_empty_raw_db`) → returns `None`, writes nothing.
  - `test_export_only_excluded`: a DB where every company is excluded → summary written, no cards.
  - `tests/test_pipeline.py`: `test_export_runs_after_dbt` (monkeypatched `export_leads` called with `(Path("x.duckdb"), Path("output"), 10)`), `test_export_skipped_when_dbt_fails`, `test_export_error_fails_pipeline`.
- [ ] **Step 2:** `uv run pytest tests/test_export.py tests/test_pipeline.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement.** Card layout exactly as spec §7, minus the `Robust:` and `Checked:` lines. Numbers per Ruling 2. Add `output/` to `.gitignore`.
- [ ] **Step 4:** `uv run pytest -q` (all pass). On the real DB: `uv run python -m src.export --out /tmp/leads-out` (outside the repo) → 2 `WATCH` cards + 8 `CLEAR` cards expected (spec §10). Read the `.md` and check every number against `mart_leads`.
- [ ] **Step 5: Commit** `LEAD-04: export leads.csv and leads.md with --top N; pipeline runs export`.

---

### Task 5 (LEAD-05): README and real-data verification

**Files:**
- Modify: `README.md`, this plan (verification notes at the end)

- [ ] **Step 1:** README "Leads" section: what each tier means (spec §4 table, analyst wording), the thresholds (vars + seed) and how to change them (`--vars` or edit `dbt_project.yml`), `python -m src.export [--top N]` and `LEADS_TOP_N`, and three queries the analyst can run on the DuckDB file, each shown with its output on the real DB:
  1. share of `flag` / `watch` per `lead_category` in the latest month (denominator: all companies, excluded included, plus a second column over eligible only);
  2. companies whose `tier = 'flag'` and `prev_tier` is distinct from `'flag'`;
  3. every month of one `jar_code`: `run_month, tier, reviews_per_employee, num_insured, avg_wage`.
  Add a "Known limitations" bullet: medians over a few tiles' worth of companies; only cafés/restaurants reach `min_peers` on the demo data.
- [ ] **Step 2:** Run `uv run pytest -q` and a full `dbt build` on the real DB. Append to this plan under "Verification": test counts, tier split per category, the eligible count, the `--top 10` card list.
- [ ] **Step 3: Commit** `LEAD-05: README leads section and real-data verification`.

## Verification

Real DB, run 2026-10:

- `uv run pytest -q`: 236 passed, 2 skipped. `dbt build`: PASS=167 WARN=0 ERROR=0.
- 30 companies, 18 eligible, 12 excluded. Tiers (flag/watch/clear/excluded): cafes_restaurants 0/2/12/3, car_service 0/0/2/6, hair_beauty 0/0/2/3.
- `--top 10` cards (tier, company, rpe_vs_median):
  WATCH 1 MAISTO INOVACIJOS 2.1x; WATCH 2 SENASIS LOKYS 2.0x;
  CLEAR 1 CULT OF BEAUTY 1.7x; 2 MOMO VILNIUS 1.5x; 3 DEACAR 1.2x; 4 RIMTAS REIKALAS 1.1x;
  5 PLIUSAI 1.1x; 6 AUGUSTINO NAMAI 1.1x; 7 RESTORANU GRUPE FORTAS 1.0x; 8 BELGAS 1.0x.
