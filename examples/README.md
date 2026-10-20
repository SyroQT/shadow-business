# Example output

The demo made these files offline from the committed snapshot
`snapshots/2026-10_20261020T140513/`, for run month 2026-10:

- `2026-10/leads.csv`: the final mart, `marts.mart_leads`.
- `2026-10/leads.md`: the analyst cards (`--top 10`).

**What the run covered**
- Central Vilnius, 3 categories, 1,248 Google Places results.
- 154 places matched to a Sodra company; Sodra data is for August 2026.

| Category | Companies | Flag | Watch | Clear | Excluded |
|---|---|---|---|---|---|
| cafés and restaurants | 57 | 1 | 3 | 31 | 22 |
| car service | 31 | 2 | 0 | 4 | 25 |
| hair and beauty | 22 | 0 | 1 | 5 | 16 |

To regenerate them (a test checks that these files match the demo):

```bash
uv run python -m src.cli demo --out examples
```
