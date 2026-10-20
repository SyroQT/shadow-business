{# One row per places month. sodra_run_id: the Sodra run selected for the same month,
   else the latest earlier month's, else null (spec section 2). #}
with places_runs as (
    {{ selected_runs('places') }}
),

sodra_runs as (
    {{ selected_runs('sodra') }}
)

select
    p.run_month,
    p.run_id as places_run_id,
    (
        select s.run_id
        from sodra_runs as s
        where s.run_month <= p.run_month
        order by s.run_month desc
        limit 1
    ) as sodra_run_id
from places_runs as p
