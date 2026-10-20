-- match_confidence is not null exactly when match_status = 'matched', and is one of the three levels.
select run_month, place_id
from {{ ref('int_places_matched') }}
where (match_confidence is not null) <> (match_status = 'matched')
   or match_confidence not in ('high', 'medium', 'low')
