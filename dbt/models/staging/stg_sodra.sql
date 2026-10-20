with selected as (
    {{ selected_runs('sodra') }}
)

select
    s.run_month,
    nullif(trim(d."jarCode"), '') as jar_code,
    d.code as sodra_code,
    d.name as company_name,
    d.municipality,
    d."ecoActCodeStr" as evrk_code,
    d."ecoActName" as evrk_name,
    try_strptime(nullif(trim(d.month), ''), '%Y%m')::date as month,
    try_cast(nullif(trim(d."avgWage"), '') as decimal(12, 2)) as avg_wage,
    try_cast(nullif(trim(d."numInsured"), '') as integer) as num_insured,
    try_cast(nullif(trim(d."avgWage2"), '') as decimal(12, 2)) as avg_wage_2,
    try_cast(nullif(trim(d."numInsured2"), '') as integer) as num_insured_2,
    try_cast(nullif(trim(d.tax), '') as decimal(14, 2)) as tax,
    d.run_id,
    d.data_month,
    d.source_url,
    d.file_sha256
from {{ source('raw', 'sodra') }} as d
inner join selected as s on s.run_id = d.run_id
where nullif(trim(d."jarCode"), '') is not null
