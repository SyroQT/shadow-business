-- Matched rows count themselves; unmatched rows carry no chain columns.
select run_month, place_id
from {{ ref('int_places_matched') }}
where (match_status = 'matched' and (places_sharing_code is null or places_sharing_code < 1))
   or (match_status <> 'matched' and (places_sharing_code is not null or chain_user_rating_count is not null))
