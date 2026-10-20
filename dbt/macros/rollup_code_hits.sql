{# Collapse occurrences to one row per (run_month, place_id, jar_code). `where_sql` filters
   int_page_code_hits first. code_context / first_url come from the preferred occurrence:
   labelled, then legal_candidate, then lowest hit_index. #}
{% macro rollup_code_hits(where_sql) -%}
with hits as (
    select
        *,
        trim(regexp_replace(before_text || jar_code || after_text, '\s+', ' ', 'g')) as code_context
    from {{ ref('int_page_code_hits') }}
    where {{ where_sql }}
),

ranked as (
    select
        *,
        row_number() over (
            partition by run_month, place_id, jar_code
            order by is_labelled desc, (page_role = 'legal_candidate') desc, hit_index, final_url
        ) as preference
    from hits
)

select
    run_month,
    place_id,
    jar_code,
    bool_or(is_labelled) as is_labelled,
    count(*)::integer as n_occurrences,
    list_sort(list_distinct(list(page_role))) as found_on_roles,
    max(final_url) filter (where preference = 1) as first_url,
    max(code_context) filter (where preference = 1) as code_context
from ranked
group by run_month, place_id, jar_code
{%- endmacro %}
