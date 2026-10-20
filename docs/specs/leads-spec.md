# Leads Step — Spec

Scope: the dbt layer from `intermediate.int_places_matched` up to the **lead list**: one row per matched company per monthly run, with a tier (`flag` / `watch` / `clear` / `excluded`), the numbers behind it, and a one-sentence reason. Plus a small export that writes the list for the analyst.

Upstream contract: `docs/specs/modelling-spec.md` §4.2 (`int_places_matched`).

Driven by `task.pdf`, four things shape the design:
- the analyst has limited capacity and must **trust the list**; a flagged honest business is costly. So a company is flagged only when **two independent signals agree** (busy in public, low declared pay), after strict eligibility filters (§2, §4);
- every lead must be **explainable**: plain numbers compared with something familiar (the category median, the minimum wage), never a composite score (§5);
- every lead must be **traceable** to its listings and its Sodra record (§6);
- the analyst runs it **monthly and compares**: each row carries last month's tier (§6).

---

## 0. Conventions

Same as the modelling spec §0, plus:

| Topic | Rule |
|---|---|
| Schemas | New schema `marts` for the analyst-facing table. `int_company_month` goes in `intermediate` |
| Materialisation | All new models are **tables** |
| Grain | One lead = one company (`jar_code`) per `run_month`. A chain's branches are listed inside the lead, not flagged one by one |
| Thresholds | Every number that decides a tier is a dbt **var** in `dbt_project.yml` (§3) or a row in the `category_lead_rules` seed. Nothing is hard-coded in SQL |
| Tests | Column tests in `schema.yml`; the tier logic is pinned by dbt **unit tests** with fixed input rows |

---

## 1. `int_company_month` (table, `intermediate`)

One row per `(run_month, jar_code)` over `int_places_matched` rows with `match_status = 'matched'`.

| column | rule |
|---|---|
| `run_month`, `jar_code` | key |
| `company_name`, `evrk_code`, `evrk_name`, `sodra_month`, `num_insured`, `avg_wage`, `tax` | from the company (identical across the company's places) |
| `sodra_run_id`, `sodra_source_url`, `sodra_file_sha256`, `places_run_id` | lineage, identical across places |
| `lead_category` | the category that occurs most often across the company's places' `categories`; ties → alphabetically first |
| `n_places` | count of places (= `places_sharing_code`) |
| `chain_user_rating_count` | as in `int_places_matched` (null review counts as 0) |
| `best_match_confidence` | best over the places: `high` > `medium` > `low` |
| `best_match_confidence_reasons` | the reasons of the place holding the best confidence (ties → lowest `place_id`) |
| `any_operational` | true if any place has `business_status = 'OPERATIONAL'` |
| `places` | `STRUCT(place_id, name, address, user_rating_count, google_maps_uri)[]`, ordered by `user_rating_count` desc, then `place_id` |
| `code_source_url`, `code_context` | from the same place as `best_match_confidence` |

---

## 2. Eligibility

A company can only be a lead if it passes every check below. The **first** failing check, in this order, is recorded as `exclusion_reason`, and `tier = 'excluded'`:

1. `weak_match`: `best_match_confidence` is not `high`, and is not `medium` with reasons exactly `['no_label']`. A code with an EVRK mismatch, an unknown EVRK, or several candidate codes probably belongs to someone else (franchisor, agency, operator).
2. `not_operational`: `any_operational` is false.
3. `no_employee_data`: `num_insured` is null.
4. `too_few_reviews`: `chain_user_rating_count` is below the category's `min_reviews` (seed, §3). A handful of reviews says nothing about how busy a place is.

`num_insured = 0` is **not** excluded: zero declared employees with many reviews is exactly the pattern of interest. The ratio uses `greatest(num_insured, 1)`, and the reason text says "0 insured employees".

---

## 3. Thresholds

dbt vars in `dbt_project.yml`:

| var | value | meaning |
|---|---|---|
| `min_monthly_wage` | `1153` | Lithuanian minimum monthly wage (MMA), EUR, 2026. Update yearly |
| `low_wage_margin` | `0.10` | "near" the minimum: `avg_wage <= 1153 × 1.10 = 1268.30` |
| `busy_multiple` | `2.0` | busy = at least 2× the category median reviews per employee |
| `min_peers` | `5` | fewest eligible companies in a category for its median to count |

Seed `dbt/seeds/category_lead_rules.csv` (one row per category in `config/categories.yaml`):

| category | min_reviews | caveat |
|---|---|---|
| cafes_restaurants | 200 | Tourist-heavy places (Old Town) collect more reviews per customer. |
| hair_beauty | 100 | Stylists often rent a chair as self-employed, so they are not the salon's insured employees. |
| car_service | 50 | Customers leave fewer reviews than in hospitality. |

A test checks every category in the data has a row in the seed.

---

## 4. Signals and tier (`mart_leads`, table, `marts`)

One row per `int_company_month` row (eligible or not), so the analyst can count shares per category.

Signals, computed for eligible rows only (null when excluded):

| column | rule |
|---|---|
| `reviews_per_employee` | `chain_user_rating_count / greatest(num_insured, 1)`, rounded to 1 decimal |
| `peer_count` | eligible companies in the same `(run_month, lead_category)` |
| `category_median_rpe` | median `reviews_per_employee` over those peers |
| `rpe_vs_median` | `reviews_per_employee / category_median_rpe`, rounded to 1 decimal |
| `is_busy` | `rpe_vs_median >= busy_multiple`; **null** when `peer_count < min_peers` |
| `is_low_wage` | `avg_wage <= min_monthly_wage × (1 + low_wage_margin)`; **null** when `avg_wage` is null (Sodra hides it for very small companies) |
| `wage_vs_minimum` | `avg_wage / min_monthly_wage`, rounded to 2 decimals |

Tier, first rule that applies:

| tier | rule | meaning for the analyst |
|---|---|---|
| `excluded` | failed §2 | not assessed; `exclusion_reason` says why |
| `flag` | `is_busy` and `is_low_wage` | look here first: busy in public **and** low declared pay |
| `watch` | `is_busy` and `is_low_wage` is not true | busy, but pay looks normal or is unknown |
| `clear` | everything else | nothing stands out (includes `is_busy` null: too few peers to compare) |

A low wage **alone** never makes a lead: part-time work is legal and lowers the average.

`lead_rank`: within `(run_month, tier)`, ordered by `rpe_vs_median` desc, then `jar_code`. Analysts work `flag` from rank 1, then `watch`.

`priority_rank`: one ranking over all **eligible** companies in a `run_month`: tier order (`flag`, `watch`, `clear`), then `rpe_vs_median` desc, then `jar_code`. Null for `excluded`. This is what `--top N` (§7) cuts. `rpe_vs_median` is computed whenever `peer_count >= 1`, even below `min_peers`, so small categories still rank; it just cannot make them `busy`.

---

## 5. Reason text

`lead_reason` (VARCHAR), one sentence built in SQL from the row's own numbers, so the analyst never needs to look up a formula.

- `flag`: `"{chain reviews} Google reviews across {n_places} place(s) and {num_insured} insured employees: {rpe} reviews per employee, {rpe_vs_median}× the {lead_category} median ({median}). Average wage €{avg_wage}, {at/below/near} the minimum wage (€1153)."`
- `watch`: the same first sentence, then `"Average wage €{avg_wage} looks normal."` or `"Average wage not published by Sodra."`
- `clear`: `"{rpe_vs_median}× the {lead_category} median; below the {busy_multiple}× threshold."` or `"Only {peer_count} comparable companies in {lead_category}; at least {min_peers} needed to compare."`
- `excluded`: a fixed sentence per `exclusion_reason`, e.g. `weak_match` → `"Company code on the website may not belong to this business ({reasons})."`

`category_caveat` (from the seed) is a separate column, shown next to the reason, not inside it.

---

## 6. Traceability and month-over-month

Columns of `mart_leads`:

| group | columns |
|---|---|
| key | `run_month`, `jar_code` |
| verdict | `tier`, `lead_rank`, `priority_rank`, `lead_reason`, `exclusion_reason`, `category_caveat`, `prev_tier` |
| signals | `reviews_per_employee`, `peer_count`, `category_median_rpe`, `rpe_vs_median`, `is_busy`, `is_low_wage`, `wage_vs_minimum` |
| public activity | `lead_category`, `n_places`, `chain_user_rating_count`, `places` |
| declared activity | `company_name`, `evrk_code`, `evrk_name`, `sodra_month`, `num_insured`, `avg_wage`, `tax` |
| match evidence | `best_match_confidence`, `best_match_confidence_reasons`, `code_source_url`, `code_context` |
| lineage | `places_run_id`, `sodra_run_id`, `sodra_source_url`, `sodra_file_sha256` |
| thresholds used | `min_monthly_wage`, `busy_multiple` (copied in, so an old month's row still explains itself after a var changes) |

`prev_tier`: the tier of the same `jar_code` in the latest earlier `run_month` present in `mart_leads`; null when the company was not there.

---

## 7. Export

`src/export.py`, called by `src/pipeline.py` after a successful `dbt build` (replacing the "export not implemented" log line). For the latest `run_month` it writes:

- `output/<run_month>/leads.csv`: every `mart_leads` row (list columns joined with `; `);
- `output/<run_month>/leads.md`: a summary table (companies per category × tier), then one card per `flag` and `watch` lead in rank order, then, when `--top N` is given, cards for the remaining companies up to `priority_rank <= N` under a heading **"Closest to the threshold (not flagged)"**:

```
FLAG #1  <company_name> (<lead_category>)                    run <run_month>
  Busy:   <chain reviews> Google reviews, <n_places> place(s); <num_insured> insured employees
          → <rpe> reviews per employee, <rpe_vs_median>× the <category> median (<median>)
  Paid:   average wage €<avg_wage>, <wage_vs_minimum>× the minimum wage (€1153)
  Match:  company code <jar_code> on <code_source_url>
          "<code_context>"
          Sodra: <company_name>, EVRK <evrk_code>, data month <sodra_month>
  Places: <name> — <google_maps_uri>   (one line per place)
  Note:   <category_caveat>
  Last month: <prev_tier or "not present">
```

Each card shows the company's **real tier** (`FLAG`, `WATCH`, `CLEAR`) and its `lead_reason`. A `clear` card's reason says why it was not flagged (e.g. "1.5× the median; below the 2.0× threshold"). `--top` never relabels a company.

Parameter: `python -m src.export [--top N]`; the pipeline passes `--top` from env `LEADS_TOP_N` (default `10`; `0` = flag and watch only). `--top` changes only how many cards are written, never the `mart_leads` table or the CSV, which always hold every row.

`output/` is committed only for the example run (a later task); otherwise git-ignored.

The README gets a "Leads" section: what each tier means, the thresholds, and three example queries the analyst can run on the DuckDB file without re-running the pipeline (share of flagged companies per category; companies that moved to `flag` this month; all leads for one `jar_code` over time).

---

## 8. Tests

Schema tests:
- `(run_month, jar_code)` unique and not null in both models;
- `tier` in (`flag`, `watch`, `clear`, `excluded`);
- `exclusion_reason` not null exactly when `tier = 'excluded'`; accepted values as in §2;
- `lead_reason` not null on every row;
- `tier = 'flag'` implies `is_busy` and `is_low_wage` (singular test);
- every `lead_category` has a row in `category_lead_rules`;
- `int_company_month` row count per `run_month` = distinct matched `jar_code` count in `int_places_matched`.

Unit tests (`mart_leads`, `int_company_month`):
- one company per tier;
- each exclusion reason, and the order (a weak match with few reviews → `weak_match`);
- `medium` with `['no_label']` is eligible; `medium` with `['no_label','evrk_mismatch']` is not;
- wage boundary: `1268.30` → low, `1268.31` → not low;
- null `avg_wage` + busy → `watch`, not `flag`;
- `peer_count = 4` → `is_busy` null → `clear` with the "too few comparable companies" reason;
- `num_insured = 0` → ratio uses 1, company stays eligible;
- two places of one company → one row, reviews summed, `places` ordered by reviews;
- `lead_category` tie → alphabetically first;
- `prev_tier` picks the latest earlier month, null for a new company.

Python tests for `src/export.py` on a tmp DuckDB built by the dbt harness: both files written; `--top 0` writes one card per flag/watch row and none for `clear`/`excluded`; `--top N` adds `clear` cards up to `priority_rank <= N`, labelled `CLEAR`, under the "Closest to the threshold" heading; `excluded` never gets a card; the CSV is identical with and without `--top`.

Unit test: `priority_rank` orders a `watch` above a `clear` with a higher `rpe_vs_median`, and is null for `excluded`.

---

## 9. Expected on the current data (sanity check, not a test)

About 20 of 43 matched companies are eligible (14 cafés/restaurants, 4 car service, 2 hair/beauty). Only cafés/restaurants reach `min_peers`. The highest `rpe_vs_median` is about 2.1, and none of the busy companies has a low wage, so the expected result is **0 `flag`, about 2 `watch`**. Car service and hair/beauty come out `clear` with "too few comparable companies". With the default `LEADS_TOP_N=10`, the demo output shows the 2 `watch` cards plus 8 `clear` cards closest to the threshold.

That is the design working as intended (strict, few false positives), but it also shows the sample is the limit: the recorded demo covered a few tiles. A full Vilnius run of the three categories is what makes the peer medians meaningful. Report the real split in the plan's verification step.

---

## 10. Out of scope (decided)

- **Employee trend** over Sodra's 8 data months as a third signal. `stg_sodra` has the history; add later as a corroborating signal.
- **Review velocity** (new reviews per month): needs two or more places runs.
- **Tourism adjustment**: no clean data source; shown as a category caveat instead.
- **A composite score**: rejected, it is harder to explain than two named signals.
- Docker, the GitHub repo, the committed example output and DECISIONS.md: separate tasks.

## 11. Open questions to settle during implementation

1. Confirm DuckDB `median()` works as a window function in the dbt-duckdb version pinned; if not, compute it in a grouped CTE and join back.
2. Decide how `avg_wage` is formatted in the reason text (two decimals, `€1,268.30` vs `€1268.30`) and keep it identical in SQL and the export.
