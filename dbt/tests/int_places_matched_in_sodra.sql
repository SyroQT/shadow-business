-- Every matched (sodra_run_id, jar_code) exists in stg_sodra.
select m.run_month, m.place_id
from {{ ref('int_places_matched') }} as m
where m.match_status = 'matched'
  and not exists (
      select 1 from {{ ref('stg_sodra') }} as s
      where s.run_id = m.sodra_run_id and s.jar_code = m.jar_code
  )
