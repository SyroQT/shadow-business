{# Lead rules (leads spec section 4). Thresholds are arguments so the sensitivity
   model can call these with each grid row; int_lead_signals passes var(...). #}

{% macro lead_is_busy(rpe_vs_median, peer_count, busy_multiple, min_peers) -%}
    (case when {{ peer_count }} < {{ min_peers }} then null else {{ rpe_vs_median }} >= {{ busy_multiple }} end)
{%- endmacro %}

{% macro lead_is_low_wage(avg_wage, min_monthly_wage, low_wage_margin) -%}
    (case when {{ avg_wage }} is null then null
          else {{ avg_wage }}::decimal(18, 4) <= ({{ min_monthly_wage }} * (1 + {{ low_wage_margin }}))::decimal(18, 4) end)
{%- endmacro %}

{% macro lead_tier(exclusion_reason, is_busy, is_low_wage) -%}
    (case
        when {{ exclusion_reason }} is not null then 'excluded'
        when {{ is_busy }} and {{ is_low_wage }} then 'flag'
        when {{ is_busy }} then 'watch'
        else 'clear'
    end)
{%- endmacro %}
