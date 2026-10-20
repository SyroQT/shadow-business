# Runs Spec: Docker, full run, snapshot, demo

## 1. Intent

Two people run this project:

- **The author** runs the live pipeline locally, with their own API keys. This mimics the production monthly scrape and must stay within the free tiers.
- **A reviewer** clones the public repo with no keys. With one Docker command they get the lead list rebuilt from a saved run, and they can read an example output without running anything.

Success: on a clean machine with only Docker, `docker compose run --rm demo` writes `output/2026-10/leads.csv` and `leads.md` in a few minutes. It makes no network calls, and the output is identical to the committed example.

## 2. Goals, non-goals, anti-goals

Goals
- One CLI with three commands: `full`, `snapshot`, `demo`.
- A saved run (snapshot) small enough to commit, from which the demo rebuilds the same mart.
- A committed example output from the final mart.
- One Docker image used for both the author's and the reviewer's runs.

Non-goals
- Scheduling, cloud deployment, CI publishing of the image.
- A new extraction method; `full` reuses `src/pipeline.py` steps.
- Snapshots of more than one run in the repo.

Anti-goals
- **The demo calls any network service, or needs a key or `.env`.**
- **`full` spends past the free tier.**
- **Keys end up in the image, the snapshot, logs or the example output.**
- **The demo writes to `data/leads.duckdb`** (the author's real database).

## 3. CLI

Entry point `python -m src.cli <command>`, also the image entrypoint. `src/pipeline.py` keeps working and becomes the body of `full`.

### 3.1 `full [--dry-run]`

1. **Budget check, before any API call.**
   - The used amount comes from `raw.run_log.api_requests` for the step, over runs whose `started_at` is in the current calendar month (UTC).
   - Per-run caps become `min(MAX_*, free_tier - used_this_month)`.
   - If the remaining amount for either API is `<= 0`, print one line per API to stderr and exit 2 before extraction starts.
2. **Free tiers.** These are the values in README "Firecrawl cost and time" and the Places pricing notes:

   | API | Monthly free tier | Setting | Notes |
   |---|---|---|---|
   | Places (Text Search Enterprise) | 1,000 requests | `PLACES_FREE_TIER=1000` | |
   | Firecrawl | 1,000 credits | `FIRECRAWL_FREE_TIER=1000` | `MAX_FIRECRAWL_PAGES` still caps one run |

   Cache hits cost nothing and are not counted. Robots and Sodra requests are free and not counted.
3. Run extract → dbt build → export, exactly as `src/pipeline.py` does today.
4. On exit 0, run `snapshot` for the new run, writing to `snapshots/<run_id>/`.

`--dry-run` prints the used, free-tier and remaining amounts per API and the caps the run would use, then exits 0 without calling anything.

### 3.2 `snapshot [--run-id ID] [--out DIR]`

- Exports one existing run from `data/leads.duckdb`. It calls no APIs.
- `--run-id` defaults to the latest run that has both a `places` and a `crawl` row in `raw.run_log`.
- `--out` defaults to `snapshots/<run_id>/`.

It writes:

| File | Contents |
|---|---|
| `places.parquet` | `raw.places` rows of the run |
| `places_errors.parquet` | `raw.places_errors` rows of the run |
| `web_pages.parquet` | `raw.web_pages` rows of the run |
| `sodra.parquet` | `raw.sodra` rows, all months, whose `jarCode` is one of the codes found on the run's pages (see below) |
| `run_log.parquet` | `raw.run_log` rows of the run, plus the Sodra run(s) whose rows are included |
| `manifest.json` | see below |

**Sodra filter.** The codes are the `jar_code`s in `int_page_code_hits`, so matching sees the same candidates as in the live run. The builder may widen the filter if the mart differs without it; the test in §6 decides.

**`manifest.json` fields:**
- `run_id`, `run_month`, `created_at`;
- row counts per file;
- the git commit;
- `sodra_source_url`;
- a note that Places content is a small demo sample.

**Rules:**
- No file in the snapshot may contain an API key. The snapshot test greps for the configured key values when they are set, and for `key=` / `Bearer `.
- The command refuses to overwrite a non-empty output directory unless `--force` is given.

### 3.3 `demo [--snapshot DIR] [--out DIR] [--top N]`

1. `--snapshot` defaults to the newest directory under `snapshots/`, by `run_id` (which sorts by time); if there are none it fails with a one-line message. Builder's call: a new `full` run adds a snapshot, and the author commits it on purpose to change the demo.
2. It creates a fresh DuckDB at `data/demo.duckdb`, replacing it if present, and loads each parquet file into its `raw.*` table.
3. It runs `dbt build` against that file. The run month comes from the loaded `raw.run_log` rows, the way `stg_places` and `stg_sodra` already select runs, so the demo does not depend on today's date. This is why `run_log.parquet` must include the Sodra run rows.
4. It exports with the existing `export_leads`, to `--out` (default `output/`). `--top` defaults to `LEADS_TOP_N`.
5. It must run with no `.env` and no keys. Settings that need keys are not loaded.

## 4. Example output

Committed under `examples/`, produced by `demo` from the committed snapshot:

- `examples/2026-10/leads.csv`: the export CSV, i.e. the final mart for the run month.
- `examples/2026-10/leads.md`: the analyst cards. (The export writes to `<out>/<run_month>/`.)
- `examples/README.md`: a few lines on what the run covers (date, area, 3 categories, company counts per tier) and how to regenerate it (`python -m src.cli demo --out examples`).

## 5. Docker

- **Image:** `Dockerfile` on `python:3.13-slim`.
  - Install uv and run `uv sync --frozen --no-dev`.
  - Copy `src/`, `dbt/`, `config/` and `snapshots/`, and nothing under `data/`.
  - `.dockerignore` excludes `.env`, `data/`, `output/`, `.venv`, `dbt/target`, `dbt/logs` and `tests/fixtures/recorded`.
- **Compose services (`docker-compose.yml`):**

  | Service | Command | Mounts | Env |
  |---|---|---|---|
  | `demo` | `demo` | `./output:/app/output` | none |
  | `full` | `full` | `./data:/app/data`, `./output:/app/output`, `./snapshots:/app/snapshots` | `env_file: .env` at runtime |
  | `snapshot` | `snapshot` | as `full` | none |

  The image is the same for all three, and `.env` is never copied into it.
- **Sodra in `full`:** the Sodra file is still downloaded by hand (Limitation 1). `SODRA_FILE` points at a path under the mounted `./data`.
- **dbt:** writes `target/` and `logs/` inside the container. The demo needs no dbt packages; if `dbt deps` becomes needed, it runs at build time.

## 6. Tests (pytest, no network)

- **demo reproduces example:** `demo --out <tmp>` produces files byte-identical to `examples/2026-10/leads.csv` and `leads.md` (the export has no timestamps).
- **demo offline:**
  - `demo` succeeds with no `.env` or keys in the environment;
  - any httpx request fails the test;
  - `data/leads.duckdb` is never opened.
- **snapshot fidelity:** build a small fixture DB, snapshot it, run `demo` from the snapshot, and check that `marts.mart_leads` equals the source DB's mart, row for row.
- **snapshot hygiene:**
  - no key strings appear in the snapshot files;
  - a non-empty output directory is refused without `--force`.
- **budget:**
  - used-this-month counts only the current calendar month and only `places` / `crawl` steps;
  - cap = `min(MAX, free - used)`;
  - remaining `<= 0` exits 2 before any client is built;
  - `--dry-run` makes no requests.
- **CLI:** unknown command or missing snapshot → one stderr line and a non-zero exit.

Docker itself is checked by hand: `docker compose build` then `docker compose run --rm demo`. The result is recorded in the plan's verification notes.

## 7. Decided

- **The repo is public, and the snapshot includes raw Places results.** The author accepts this as a small demo sample (171 places). DECISIONS.md Limitation 3 gets a line saying so.
- **The committed snapshot is the existing run `2026-10_20261019T173429`.** It has all 3 categories: cafés 17 companies, car service 8, hair and beauty 5; 2 WATCH.
- **The demo is for a reviewer with no keys; `full` is for the author with keys.**

## 8. Left to the builder

- Module layout under `src/` (e.g. `src/cli.py`, `src/snapshot.py`, `src/budget.py`).
- How the budget caps are passed into the existing clients (the `Settings` override is fine).
- Parquet column types (DuckDB `COPY ... (FORMAT parquet)` defaults are fine, as long as the §6 fidelity test passes).
- README section text: "Running" with the three commands and the Docker commands.
