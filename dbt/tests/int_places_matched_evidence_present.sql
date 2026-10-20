-- Every matched row carries evidence: a code_context showing jar_code, and the page it came from.
select run_month, place_id
from {{ ref('int_places_matched') }}
where match_status = 'matched'
  and (code_context is null or position(jar_code in code_context) = 0 or code_source_url is null)
