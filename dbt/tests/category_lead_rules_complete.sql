-- Every lead_category present in int_company_month must have a category_lead_rules row.
select distinct m.lead_category
from {{ ref('int_company_month') }} as m
left join {{ ref('category_lead_rules') }} as r on r.category = m.lead_category
where r.category is null
