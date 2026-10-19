> Demo report generated from recorded fixtures (tests/fixtures/recorded/), not a live run.
> Crawl rows for sites outside the 10 recorded homepages are synthetic "not recorded" errors, so the crawl share and error counts are not real coverage.

# Extraction data-quality report

Run: 2026-10_20261019T154232

## Places per category

| category | rows | unique |
|---|---|---|
| cafes_restaurants | 103 | 103 |

Distinct places: 103

Website share: 91.3% (94 of 103)

Crawled share: 3.9% (4 of 103)

## Skip reasons (home pages)

| skip_reason | pages |
|---|---|
| error | 90 |
| no_website | 9 |
| (crawled) | 4 |

## Saturated tiles and coverage gaps

| category | saturated_tiles | coverage_gaps |
|---|---|---|
| cafes_restaurants | 1 | 1 |

## Sodra

Sodra rows: 20

jarCode share: 80.0%

| data_month | rows | jarCode share |
|---|---|---|
| 202601 | 10 | 80.0% |
| 202602 | 10 | 80.0% |

## Steps

| step | status | api_requests | cache_hits | duration | notes |
|---|---|---|---|---|---|
| sodra | ok | 0 | 0 | 0m 0s |  |
| places | ok | 6 | 0 | 0m 0s | cafes_restaurants:saturated_tiles=1; cafes_restaurants:coverage_gaps=1 |
| crawl | partial | 80 | 0 | 0m 0s | robots_requests=70; robots_cache_hits=0; firecrawl_errors=76; firecrawl_error_top=not recorded |
| robots | - | 70 | 0 | - |  |

Total duration: 0m 0s
