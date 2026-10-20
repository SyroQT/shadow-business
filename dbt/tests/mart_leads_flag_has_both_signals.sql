-- A flag needs both signals (leads spec section 9).
select run_month, jar_code
from {{ ref('mart_leads') }}
where tier = 'flag' and not (coalesce(is_busy, false) and coalesce(is_low_wage, false))
