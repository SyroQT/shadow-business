{# One row per int_lead_signals row: tier, ranks, analyst reason text and prev_tier
   (leads spec sections 4-6). Match review and robustness columns come with the validation plan. #}
with scored as (
    select
        *,
        {{ lead_tier('exclusion_reason', 'is_busy', 'is_low_wage') }} as tier
    from {{ ref('int_lead_signals') }}
),

months as (
    select
        run_month,
        lag(run_month) over (order by run_month) as prev_month
    from (select distinct run_month from scored)
),

ranked as (
    select
        s.*,
        m.prev_month,
        case when s.tier <> 'excluded' then
            row_number() over (
                partition by s.run_month, s.tier
                order by s.rpe_vs_median desc nulls last, s.jar_code
            )
        end::integer as lead_rank,
        case when s.tier <> 'excluded' then
            row_number() over (
                partition by s.run_month, (s.tier = 'excluded')
                order by
                    case s.tier when 'flag' then 1 when 'watch' then 2 else 3 end,
                    s.rpe_vs_median desc nulls last, s.jar_code
            )
        end::integer as priority_rank
    from scored as s
    join months as m using (run_month)
),

texted as (
    select
        *,
        {{ var('busy_multiple') }} as busy_multiple,
        -- first sentence shared by flag and watch
        chain_user_rating_count::varchar || ' Google reviews across ' || n_places::varchar
            || ' place(s) and ' || num_insured::varchar || ' insured employees: '
            || printf('%.1f', reviews_per_employee) || ' reviews per employee, '
            || printf('%.1f', rpe_vs_median) || '× the ' || category_label || ' median ('
            || printf('%.1f', category_median_rpe) || ').' as busy_sentence
    from ranked
)

select
    run_month,
    jar_code,
    tier,
    lead_rank,
    priority_rank,
    case tier
        when 'flag' then busy_sentence || ' Average wage €' || printf('%.2f', avg_wage) || ', '
            || case
                when avg_wage < min_monthly_wage then 'below'
                when avg_wage = min_monthly_wage then 'at'
                else 'near'
            end || ' the minimum wage (€' || min_monthly_wage || ').'
        when 'watch' then busy_sentence || case
                when is_low_wage is null then ' Average wage not published by Sodra.'
                else ' Average wage €' || printf('%.2f', avg_wage) || ' looks normal.'
            end
        when 'clear' then case
                when peer_count < {{ var('min_peers') }} then 'Only ' || peer_count::varchar
                    || ' comparable companies in ' || category_label || '; at least '
                    || {{ var('min_peers') }} || ' needed to compare.'
                when rpe_vs_median is null then 'No reviews-per-employee comparison available in '
                    || category_label || '.'
                else printf('%.1f', rpe_vs_median) || '× the ' || category_label
                    || ' median; below the ' || printf('%.1f', busy_multiple) || '× threshold.'
            end
        else case exclusion_reason
            when 'weak_match' then 'Company code on the website may not belong to this business ('
                || coalesce(nullif(array_to_string(best_match_confidence_reasons, ', '), ''),
                            best_match_confidence || ' confidence') || ').'
            when 'not_operational' then 'No place of this company is currently operational.'
            when 'no_employee_data' then 'Sodra has no insured-employee count for this company.'
            when 'too_few_reviews' then 'Only ' || chain_user_rating_count::varchar
                || ' Google reviews; at least ' || min_reviews::varchar || ' needed for '
                || category_label || '.'
        end
    end as lead_reason,
    exclusion_reason,
    category_caveat,
    (select t.tier from scored as t
        where t.run_month = texted.prev_month and t.jar_code = texted.jar_code) as prev_tier,
    reviews_per_employee,
    peer_count,
    category_median_rpe,
    rpe_vs_median,
    is_busy,
    is_low_wage,
    wage_vs_minimum,
    lead_category,
    n_places,
    chain_user_rating_count,
    places,
    company_name,
    evrk_code,
    evrk_name,
    sodra_month,
    num_insured,
    avg_wage,
    tax,
    best_match_confidence,
    best_match_confidence_reasons,
    code_source_url,
    code_context,
    places_run_id,
    sodra_run_id,
    sodra_source_url,
    sodra_file_sha256,
    min_monthly_wage,
    busy_multiple
from texted
