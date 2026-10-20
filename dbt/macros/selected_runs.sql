{# Relation (run_month, run_id): per month, the newest non-failed run of a step.
   run_month is the YYYY-MM prefix of run_id. Use as a subquery or CTE body. #}
{% macro selected_runs(step) -%}
    select
        substr(run_id, 1, 7) as run_month,
        max(run_id) as run_id
    from {{ source('raw', 'run_log') }}
    where step = '{{ step }}'
      and status <> 'failed'
      {%- if step == 'sodra' %}
      {# a duplicate-file run is logged ok but loads no rows; it must not shadow the run that has them #}
      and run_id in (select distinct run_id from {{ source('raw', 'sodra') }})
      {%- endif %}
    group by 1
{%- endmacro %}
