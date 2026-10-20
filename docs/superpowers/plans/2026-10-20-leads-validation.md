# Leads Validation (sensitivity + match precision) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show the analyst why the lead list can be trusted: a threshold sensitivity grid with a per-company robustness count, and a hand-checked match precision whose `wrong` verdicts exclude companies.

**Architecture:**
- **Sensitivity:** `mart_threshold_sensitivity` cross-joins the eligible rows of `int_lead_signals` with a 9-row grid seed and recomputes the tier through the existing lead macros. `mart_leads` gains the robustness counts.
- **Match review:** `src/match_review.py sample` writes a review CSV into a dbt seed. `int_lead_signals` reads the verdicts, and `mart_match_precision` summarises them.
- **Export:** `leads.md` gains both tables and two card lines.

**Tech Stack:** as the core plan.

**Spec:** `docs/specs/leads-spec.md` §8, the "Validation tests" list in §9, the `match_rejected` rule in §2, the `match_review_*` / robustness columns in §6 and the §7 card lines. **Depends on** `2026-10-20-leads-core.md` being complete: `int_lead_signals`, `mart_leads`, the macros `lead_is_busy` / `lead_is_low_wage` / `lead_tier`, `src/export.py::export_leads`, `tests/dbt_harness.py::load_lead_demo`.

## Global Constraints

- Branch `leads`. Commit messages prefixed `VAL-NN:`, ending with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Everything in the core plan's Global Constraints still applies (no `.env`, no network, no dbt packages, vars not literals, unit tests in YAML, `output/` git-ignored).
- The tier rule exists once: the sensitivity model calls the same macros as `int_lead_signals` / `mart_leads`, never a copy.
- Verdicts in the committed `match_review.csv` are the user's. The implementer never fills `verdict`; the controller may pre-fill a draft only when the user asks, and the user confirms it before commit.
- `uv run pytest -q` stays green after every task; record the count before VAL-01.

## Rulings (made while planning)

1. **Empty review seed.** `dbt/seeds/match_review.csv` is committed with the header only, so `dbt build` works before any review. `column_types` in `_seeds.yml` set every column to `varchar`, so an empty file still creates typed columns. `sample` overwrites a header-only file and refuses a file with at least one data row (spec "refuses to overwrite" read as "refuses to destroy review work"). Cost if wrong: one condition.
2. **Sample size `n` is the total.** Lows first (by `md5(place_id)`), then `high` / `medium` alternately (each by `md5(place_id)`), stopping at `n`; if a level runs out, the other continues.
3. **Precision levels.** `mart_match_precision.level` in `high`, `medium`, `low`, `eligible`, `stale`. `eligible` = places whose own confidence passes spec §2 check 2 (`high`, or `medium` with reasons exactly `['no_label']`). Uses the latest `run_month` of `int_places_matched`.
4. **Grid includes the defaults.** Seed rows are the 9 pairs of `{1.5, 2.0, 3.0}` × `{0.00, 0.10, 0.20}`; `settings_total` = row count of the seed, not a literal 9.
5. **Excluded companies have no sensitivity rows.** `settings_as_lead` / `settings_total` are null for `excluded`.

## Review Focus

1. **Review CSV edited by hand in a spreadsheet**: extra spaces or capitals in `verdict` (`" Correct"`). Verdicts are trimmed and lower-cased in staging; anything else fails the accepted-values test with a clear message. Unit test `verdict_normalised` (Task 2).
2. **A reviewed place that is no longer matched next month**: its verdict lands in `stale`, and it does not crash or exclude anything. Unit test `stale_verdict` (Task 2).
3. **A company with two places, one reviewed `wrong`, one `correct`**: the company is excluded (`wrong` wins). Unit test `wrong_wins` (Task 2).
4. **Running `sample` twice**: the second run refuses with a non-zero exit and leaves the file untouched once it holds a data row. Test `test_sample_refuses_reviewed_file` (Task 2).
5. **Thresholds changed via `--vars`** to a pair not in the grid: the build still passes, but the "grid contains the current setting" test fails, naming the missing pair. Test `test_grid_must_contain_current_vars` (Task 1).

---

## File Structure

```
dbt/seeds/threshold_grid.csv
dbt/seeds/match_review.csv                     # header only until the user reviews
dbt/seeds/_seeds.yml                           # + both seeds
dbt/models/marts/mart_threshold_sensitivity.sql
dbt/models/marts/mart_match_precision.sql
dbt/models/marts/mart_leads.sql                # + robustness, review columns
dbt/models/intermediate/int_lead_signals.sql   # + match_rejected, review columns
dbt/models/staging/stg_match_review.sql        # trimmed / lower-cased verdicts (view)
dbt/tests/threshold_grid_has_current_vars.sql
dbt/tests/sensitivity_default_equals_mart.sql
src/match_review.py
src/export.py                                  # + grid, precision, Robust/Checked lines
tests/test_dbt_validation.py
tests/test_match_review.py
tests/test_export.py                           # + validation cases
README.md
```

---

### Task 1 (VAL-01): threshold sensitivity

**Files:**
- Create: `dbt/seeds/threshold_grid.csv`, `dbt/models/marts/mart_threshold_sensitivity.sql`, `dbt/tests/threshold_grid_has_current_vars.sql`, `dbt/tests/sensitivity_default_equals_mart.sql`, `tests/test_dbt_validation.py`
- Modify: `dbt/seeds/_seeds.yml`, `dbt/models/marts/mart_leads.sql`, `dbt/models/marts/_marts.yml`

**Interfaces:**
- Consumes: `int_lead_signals` (eligible rows: `exclusion_reason is null`), macros `lead_is_busy(rpe_vs_median, peer_count, busy_multiple, min_peers)`, `lead_is_low_wage(avg_wage, min_monthly_wage, low_wage_margin)`, `lead_tier(exclusion_reason, is_busy, is_low_wage)`.
- Produces: `marts.mart_threshold_sensitivity(run_month, busy_multiple DOUBLE, low_wage_margin DOUBLE, jar_code, is_busy, is_low_wage, tier)`; `mart_leads.settings_as_lead INTEGER`, `mart_leads.settings_total INTEGER`.

- [ ] **Step 1: Write failing tests.**
  - Unit test in `_marts.yml` (model `mart_threshold_sensitivity`, inputs `ref('int_lead_signals')`, `ref('threshold_grid')` with the 9 rows): one eligible company with `rpe_vs_median 1.8`, `peer_count 5`, `avg_wage 2000` → `watch` in the three `1.5` rows, `clear` in the six others.
  - Unit test (model `mart_leads`): the same company's sensitivity rows → `settings_as_lead: 3`, `settings_total: 9`; an excluded company → both null.
  - `tests/test_dbt_validation.py::test_sensitivity_on_lead_demo(tmp_path)`: `load_lead_demo` + `build` → 9 × 6 eligible = 54 sensitivity rows; the flag company has `settings_as_lead = 9` (rpe ≈ 6.7×, wage 1000).
  - `test_grid_must_contain_current_vars(tmp_path)`: `run_dbt(["build", "--vars", "{busy_multiple: 2.5}"], db)` exits non-zero and its stdout names `threshold_grid_has_current_vars`.
- [ ] **Step 2:** Run them. Expected: FAIL.
- [ ] **Step 3: Implement** the seed (Ruling 4; `_seeds.yml` types double), the model (grid × eligible signals, macros with the grid row's `busy_multiple` / `low_wage_margin` the var for `min_peers` and the row's yearly `min_monthly_wage` column), the two `mart_leads` columns, schema test `unique_combination(run_month, busy_multiple, low_wage_margin, jar_code)`, and the singular tests: `threshold_grid_has_current_vars` (fails when no row equals `(var('busy_multiple'), var('low_wage_margin'))`) and `sensitivity_default_equals_mart` (for that row, every sensitivity `tier` equals `mart_leads.tier`).
- [ ] **Step 4:** `uv run pytest -q` (all pass); real-DB build. Record the 3×3 flag/watch counts in the plan's Verification section.
- [ ] **Step 5: Commit** `VAL-01: threshold sensitivity grid and robustness counts`.

---

### Task 2 (VAL-02): manual match review

**Files:**
- Create: `dbt/seeds/match_review.csv` (header only), `dbt/models/staging/stg_match_review.sql`, `dbt/models/marts/mart_match_precision.sql`, `src/match_review.py`, `tests/test_match_review.py`
- Modify: `dbt/seeds/_seeds.yml`, `dbt/models/staging/_staging.yml`, `dbt/models/intermediate/int_lead_signals.sql`, `dbt/models/intermediate/_intermediate.yml`, `dbt/models/marts/mart_leads.sql`, `dbt/models/marts/_marts.yml`, `tests/test_dbt_validation.py`

**Interfaces:**
- Consumes: `int_places_matched`, `int_lead_signals`, `src.settings.load_settings_or_exit`.
- Produces:
  - `match_review.csv` header: `place_id,jar_code,place_name,website_uri,code_context,company_name,evrk_name,match_confidence,verdict,note`.
  - `staging.stg_match_review(place_id, jar_code, verdict, note)`: `verdict` trimmed + lower-cased, blank → null.
  - `int_lead_signals` gains `match_review_verdict`, `match_review_note` (company-level, `wrong` > `unsure` > `correct`; note from the winning verdict, ties → lowest `place_id`) and `exclusion_reason = 'match_rejected'` as the **first** check. `mart_leads` carries both columns and the `match_rejected` sentence (spec §5: `"Match checked by hand and rejected: {note}."`).
  - `marts.mart_match_precision(level, n_reviewed, n_correct, n_wrong, n_unsure, precision DOUBLE)`, levels per Ruling 3, one row per level always (zeros when nothing reviewed).
  - `src/match_review.py`: `sample(db_path: Path, out: Path, n: int = 20) -> int` (rows written) and `main(argv) -> int` with subcommand `sample [--n N] [--out PATH]` (default `dbt/seeds/match_review.csv`). Exit 1 with a message when the file holds a data row (Ruling 1). `python -m src.match_review sample` runs it.

- [ ] **Step 1: Write failing tests.**
  - `tests/test_match_review.py` (on a harness-built DB with places at all three confidence levels):
    - `test_sample_deterministic`: two samples into two paths → identical bytes.
    - `test_sample_lows_first`: with 2 `low`, 5 `high`, 5 `medium` and `n=6` → rows 1–2 are the lows, then `high`, `medium` alternating.
    - `test_sample_columns`: header exactly as above, `verdict` and `note` empty.
    - `test_sample_overwrites_header_only` / `test_sample_refuses_reviewed_file`: header-only file → overwritten; a file with one data row → exit 1, file bytes unchanged.
  - Unit tests:
    - `int_lead_signals` `wrong_excludes_even_if_flag`: busy, low wage, verdict `wrong` → `exclusion_reason: match_rejected`.
    - `wrong_wins`: company with `p1 correct`, `p2 wrong` → `match_review_verdict: wrong`.
    - `mart_match_precision` `precision_ignores_unsure`: high has `correct, correct, wrong, unsure, blank` → `n_reviewed 4, n_correct 2, n_wrong 1, n_unsure 1, precision 0.667` (3 decimals); `medium` with only `unsure` → `precision: null`.
    - `stale_verdict`: a verdict whose `(place_id, jar_code)` is not matched in the latest month → counted in `stale`, not in any confidence level.
  - `stg_match_review` unit test `verdict_normalised` in `_staging.yml`: input rows `" Correct "`, `"WRONG"`, `""` → `correct`, `wrong`, null.
  - Schema tests: `stg_match_review` `unique_combination(place_id, jar_code)`, `verdict` accepted values `correct` / `wrong` / `unsure` (nulls allowed); `exclusion_reason` accepted values gain `match_rejected`; `mart_match_precision.level` unique.
- [ ] **Step 2:** Run them. Expected: FAIL.
- [ ] **Step 3: Implement** in the order seed → staging view → `int_lead_signals` → `mart_leads` → `mart_match_precision` → `src/match_review.py`.
- [ ] **Step 4:** `uv run pytest -q` (all pass). Real DB: `uv run python -m src.match_review sample --out /tmp/match_review.csv` (outside the repo; do **not** write the committed seed) and check it has 20 rows, every `low` match first. Real-DB build: `mart_match_precision` shows `n_reviewed = 0` on every row.
- [ ] **Step 5: Commit** `VAL-02: manual match review sample, precision mart, match_rejected exclusion`.

---

### Task 3 (VAL-03): export, README, verification

**Files:**
- Modify: `src/export.py`, `tests/test_export.py`, `README.md`, this plan (Verification)

**Interfaces:**
- Consumes: `mart_threshold_sensitivity`, `mart_match_precision`, `mart_leads.settings_as_lead` / `settings_total` / `match_review_verdict`.
- Produces: `leads.md` sections in spec §7 order: summary table, **sensitivity grid**, **match precision**, cards. Cards gain `Robust:` (always on flag/watch/clear cards) and `Checked:` (only when `match_review_verdict` is not null).

- [ ] **Step 1: Write failing tests** in `tests/test_export.py` (same module fixture as the core plan):
  - `test_grid_table`: a 3×3 table, rows `1.5` / `2.0` / `3.0`, columns `0%` / `10%` / `20%`, cell `"<flag> flag / <watch> watch"`, the `2.0` × `10%` cell in `**bold**`; on the lead demo the bold cell reads `**1 flag / 1 watch**`.
  - `test_precision_table`: five rows `high`, `medium`, `low`, `eligible`, `stale`; `precision` shown as `—` when null, else a percentage with no decimals.
  - `test_robust_line`: the flag card has `Robust: flag or watch in 9 of 9 threshold settings`.
  - `test_checked_line_only_when_reviewed`: no `Checked:` line on the lead demo; in a copy of the built DB, `update marts.mart_leads set match_review_verdict = 'correct' where tier = 'flag'`, export again → the flag card has `Checked: match checked by hand: correct` (this tests the rendering only; the verdict logic is pinned by VAL-02's unit tests).
- [ ] **Step 2:** Run. Expected: FAIL.
- [ ] **Step 3: Implement** the two tables and two lines.
- [ ] **Step 4: README** "Validation" subsection under "Leads": what each check answers, how to run `python -m src.match_review sample`, how to fill the CSV (`correct` / `wrong` / `unsure`, note), that a `wrong` verdict excludes the company from then on, and what neither check proves (spec §8 intro). Then `uv run pytest -q` and a real-DB build + export to `/tmp/leads-out`; append to Verification: the 3×3 grid on real data, the robustness of each `watch`, test counts.
- [ ] **Step 5: Commit** `VAL-03: sensitivity and precision in leads.md; README validation section`.

## After this plan (user step)

The user reviews the sample (about 30 minutes). Then `match_review.csv` is copied into `dbt/seeds/`, rebuilt, and committed with the resulting precision table. This is not an implementer task.

## Verification

(filled in by Tasks 1, 2 and 3)
