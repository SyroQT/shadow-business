-- Every company row rolls up at least one place.
select run_month, jar_code, n_places
from {{ ref('int_company_month') }}
where n_places < 1
