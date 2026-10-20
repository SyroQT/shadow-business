# Example output

`2026-10/leads.csv` (the final mart, `marts.mart_leads`, for run month 2026-10) and
`2026-10/leads.md` (the analyst cards, `--top 10`), made offline by `demo` from the committed
snapshot `snapshots/2026-10_20261019T173429/`.

The run covers Vilnius, 3 categories and 171 Google Places results, matched to Sodra data for
August 2026:

| Category | Companies | Watch | Clear | Excluded |
|---|---|---|---|---|
| cafés and restaurants | 17 | 2 | 12 | 3 |
| car service | 8 | 0 | 2 | 6 |
| hair and beauty | 5 | 0 | 2 | 3 |

No company is flagged. To regenerate (a test checks that these files match):

```bash
uv run python -m src.cli demo --out examples
```
