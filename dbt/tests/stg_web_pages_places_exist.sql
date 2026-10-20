-- Every (run_month, place_id) of stg_web_pages must exist in stg_places.
select w.run_month, w.place_id
from {{ ref('stg_web_pages') }} as w
left join (
    select distinct run_month, place_id from {{ ref('stg_places') }}
) as p using (run_month, place_id)
where p.place_id is null
group by 1, 2
