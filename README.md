# Lead extraction

This project finds Vilnius businesses that look busy in public but declare low pay. It starts
from Google Places, crawls each business's website for its company code, matches that code to
public Sodra (social insurance) data, and ranks the companies in a dbt mart.

A lead is a reason to look at a business, not proof of anything.

## Quick start (no keys, no network)

```bash
docker compose run --rm demo          # or: uv run python -m src.cli demo
```

- **What it does:** rebuilds the lead list from the committed snapshot in `snapshots/`, in
  about 10 s.
- **What it uses:** that snapshot is one real run: 1,248 Google Places results in 3 categories.
  The demo builds its own `data/demo.duckdb`.
- **What it writes:** `output/2026-10/leads.csv` (the final mart) and `leads.md` (analyst cards).
- **Without running anything:** the same files are in [`examples/`](examples/README.md).

## For reviewers

**Where to look**

1. [`examples/2026-10/leads.md`](examples/2026-10/leads.md): the 3 flags and the watch cases as analyst cards. Each card shows the reason, the Maps link, the company code with the page and quote it came from, and the Sodra row.
2. [`examples/2026-10/leads.csv`](examples/2026-10/leads.csv): the full mart (every company, including `clear` and `excluded`, with `exclusion_reason`).
3. [`DECISIONS.md`](DECISIONS.md): decisions, rejected alternatives, limitations.
4. To see it live: `docker compose run --rm demo`, about 10 s.

## Run types

| Command                                                         | Who               | What                                                                         |
| --------------------------------------------------------------- | ----------------- | ---------------------------------------------------------------------------- |
| `python -m src.cli demo [--snapshot DIR] [--out DIR] [--top N]` | reviewer          | newest snapshot → fresh DuckDB → dbt build → export (offline)                |
| `python -m src.cli full [--dry-run]`                            | author, with keys | live extract → dbt → export within the free API tiers, then saves a snapshot |
| `python -m src.cli snapshot [--run-id ID]`                      | author            | saves an existing run from `data/leads.duckdb` as parquet (no API calls)     |

**What `full` needs**

- `GOOGLE_API_KEY`, `FIRECRAWL_API_KEY` and `SODRA_FILE` in `.env` (see `.env.example`).
- The Sodra zip, downloaded by hand from https://atvira.sodra.lt/imones/rinkiniai/index.html.
  It sits behind Cloudflare.

**How `full` stays in the free tiers**

- Before any call, it subtracts this month's logged usage from the free tier. The limits are
  1,000 Places requests and 1,000 Firecrawl credits.
- It exits with code 2 if a tier is used up, or if the Places worst case is above the cap.
- `--dry-run` prints the numbers and stops.

Docker (`.env` is read at runtime, never copied into the image):

```bash
docker compose run --rm demo
docker compose run --rm full --dry-run    # mounts ./data; set SODRA_FILE to a path under data/
```

## How it works

```
Google Places (quadtree over central Vilnius) ─┐
Firecrawl (home + legal pages, robots.txt)  ───┼─► raw.* (DuckDB, append-only) ─► dbt ─► marts.mart_leads ─► CSV + cards
Sodra monthly zip                            ──┘
```

**Extract and match**

- **Extraction:** `src/extract/`. Categories and the area are set in `config/categories.yaml`.
- **Matching:** a standalone 9-digit company code found on the business's own pages, which
  must exist in Sodra.

**Lead tiers**

A company is flagged only when two signals agree: it is **busy** and it is **low-wage**.

| Tier       | Meaning                                             |
| ---------- | --------------------------------------------------- |
| `flag`     | Look here first: busy and low-wage.                 |
| `watch`    | Busy, but the wage looks normal or isn't published. |
| `clear`    | Nothing stands out.                                 |
| `excluded` | Not assessed. `exclusion_reason` says why.          |

- **Busy:** reviews per insured employee are at least 2× the category median.
- **Low-wage:** the average wage is at most 10% above that year's minimum wage.

**Where the thresholds live**

- dbt vars in `dbt/dbt_project.yml`: `busy_multiple`, `low_wage_margin`, `min_peers`.
- Seeds: `minimum_wage.csv` (one row per year) and `category_lead_rules.csv`.
- To try another value without editing files:
  `uv run dbt build --project-dir dbt --profiles-dir dbt --vars '{busy_multiple: 1.5}'`

## Repo map

| Path                      | What                                                                              |
| ------------------------- | --------------------------------------------------------------------------------- |
| `src/cli.py`              | `full` / `demo` / `snapshot`                                                      |
| `src/extract/`            | Places, crawl, Sodra and the data-quality report (`python -m src.extract report`) |
| `dbt/`                    | staging → intermediate (matching, signals) → `mart_leads`                         |
| `snapshots/`, `examples/` | committed demo input and its output                                               |
| `docs/specs/`             | requirements per stage                                                            |
| `DECISIONS.md`            | design decisions, alternatives, limitations                                       |

## Tests

```bash
uv run pytest            # offline: unit, dbt and demo tests; examples/ must match the demo
LIVE=1 uv run pytest     # also live API tests (spends quota)
```
