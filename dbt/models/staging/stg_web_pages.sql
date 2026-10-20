with selected as (
    {{ selected_runs('places') }}
)

select
    s.run_month,
    w.place_id,
    w.page_role,
    w.requested_url,
    w.final_url,
    w.http_status,
    w.skip_reason,
    (w.skip_reason is null and w.markdown is not null) as is_fetched,
    w.markdown,
    w.content_sha256,
    w.run_id,
    w.retrieved_at
from {{ source('raw', 'web_pages') }} as w
inner join selected as s on s.run_id = w.run_id
