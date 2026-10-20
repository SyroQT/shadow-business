{{ config(severity='warn') }}
-- Raw numeric values that are non-empty but became null after try_cast in stg_sodra.
select
    d.run_id, d."jarCode", d.month,
    d."avgWage", d."numInsured", d."avgWage2", d."numInsured2", d.tax
from {{ source('raw', 'sodra') }} as d
inner join ({{ selected_runs('sodra') }}) as s on s.run_id = d.run_id
where nullif(trim(d."jarCode"), '') is not null
  and (
    (nullif(trim(d."avgWage"), '') is not null
        and try_cast(nullif(trim(d."avgWage"), '') as decimal(12, 2)) is null)
    or (nullif(trim(d."numInsured"), '') is not null
        and try_cast(nullif(trim(d."numInsured"), '') as integer) is null)
    or (nullif(trim(d."avgWage2"), '') is not null
        and try_cast(nullif(trim(d."avgWage2"), '') as decimal(12, 2)) is null)
    or (nullif(trim(d."numInsured2"), '') is not null
        and try_cast(nullif(trim(d."numInsured2"), '') as integer) is null)
    or (nullif(trim(d.tax), '') is not null
        and try_cast(nullif(trim(d.tax), '') as decimal(14, 2)) is null)
  )
