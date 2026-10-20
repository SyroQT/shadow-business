-- exclusion_reason is set exactly when tier = 'excluded' (leads spec section 9).
select run_month, jar_code
from {{ ref('mart_leads') }}
where (tier = 'excluded') <> (exclusion_reason is not null)
