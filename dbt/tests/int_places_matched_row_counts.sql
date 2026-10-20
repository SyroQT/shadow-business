-- Per run_month, int_places_matched has exactly one row per distinct place in stg_places.
with expected as (
    select run_month, count(distinct place_id) as n from {{ ref('stg_places') }} group by 1
),

actual as (
    select run_month, count(*) as n from {{ ref('int_places_matched') }} group by 1
)

select coalesce(e.run_month, a.run_month) as run_month, e.n as expected_rows, a.n as actual_rows
from expected as e
full join actual as a using (run_month)
where e.n is distinct from a.n
