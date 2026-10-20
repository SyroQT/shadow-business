-- Per run_month, int_company_month has one row per distinct matched jar_code of int_places_matched.
with expected as (
    select run_month, count(distinct jar_code) as n
    from {{ ref('int_places_matched') }}
    where match_status = 'matched'
    group by 1
),

actual as (
    select run_month, count(*) as n from {{ ref('int_company_month') }} group by 1
)

select coalesce(e.run_month, a.run_month) as run_month, e.n as expected_rows, a.n as actual_rows
from expected as e
full join actual as a using (run_month)
where e.n is distinct from a.n
