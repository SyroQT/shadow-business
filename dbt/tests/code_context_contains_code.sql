{# code_context must show the code it is evidence for #}
select 'int_page_company_codes' as model, run_month, place_id, jar_code
from {{ ref('int_page_company_codes') }}
where position(jar_code in code_context) = 0
union all
select 'int_page_company_codes_unmatched', run_month, place_id, jar_code
from {{ ref('int_page_company_codes_unmatched') }}
where position(jar_code in code_context) = 0
