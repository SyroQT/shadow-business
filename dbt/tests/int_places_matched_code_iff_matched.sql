-- jar_code is not null exactly when match_status = 'matched'.
select run_month, place_id
from {{ ref('int_places_matched') }}
where (jar_code is not null) <> (match_status = 'matched')
