{# Relation (run_month, run_id): per month, the newest non-failed run of a step that
   actually landed rows in that step's raw table. run_month is the YYYY-MM prefix of
   run_id. Use as a subquery or CTE body. #}
{% macro selected_runs(step) -%}
    {%- set tables = {'places': 'places', 'sodra': 'sodra'} -%}
    {%- if step not in tables -%}
        {{ exceptions.raise_compiler_error("selected_runs: unknown step '" ~ step ~ "'") }}
    {%- endif -%}
    select
        substr(run_id, 1, 7) as run_month,
        max(run_id) as run_id
    from {{ source('raw', 'run_log') }}
    where step = '{{ step }}'
      and status <> 'failed'
      and run_id in (select distinct run_id from {{ source('raw', tables[step]) }})
    group by 1
{%- endmacro %}
