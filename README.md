# Lead extraction

## How to run

Places extraction (needs `GOOGLE_API_KEY` in `.env`):

```
uv run python -m src.extract.places                       # all categories
uv run python -m src.extract.places --category cafes      # repeatable
uv run python -m src.extract.places --run-id 2026-10_20261019T141502   # reuse a run id
```

Prints JSON log lines, then one `places_summary` line per category (`places`, `unique_places`,
`with_website`, `saturated_tiles`, `coverage_gaps`). Exit code 1 if the step failed, 2 on an
unknown category. A capped or partial run prints a `warning` line.

## Cost per run

- Places API (New) Text Search with a field mask including `websiteUri`, `rating`, `priceLevel`
  and `regularOpeningHours` bills at the Enterprise SKU.
- 1,000 free events per month, then $35 per 1,000. Each page fetched is 1 request.
- Cost = max(0, requests - 1000) x $0.035.
- Estimate for Vilnius at `tile_km` 8.0: 600-1,200 requests.
- Measured: TBD after first live run.
- `MAX_PLACES_REQUESTS` (default 1000) stops the run with status `capped`.
