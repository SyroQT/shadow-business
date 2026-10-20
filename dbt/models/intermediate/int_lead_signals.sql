{# One row per int_company_month row: eligibility (leads spec section 2, checks 2-5)
   and the busy / low-wage signals (section 4), null when excluded. #}
with base as (
    select
        m.*,
        r.min_reviews,
        r.caveat as category_caveat,
        r.label as category_label,
        case
            when not (
                m.best_match_confidence = 'high'
                or (m.best_match_confidence = 'medium'
                    and coalesce(m.best_match_confidence_reasons = ['no_label']::varchar[], false))
            ) then 'weak_match'
            when not m.any_operational then 'not_operational'
            when m.num_insured is null then 'no_employee_data'
            when m.chain_user_rating_count < r.min_reviews then 'too_few_reviews'
        end as exclusion_reason
    from {{ ref('int_company_month') }} as m
    left join {{ ref('category_lead_rules') }} as r on r.category = m.lead_category
),

rpe as (
    select
        *,
        case when exclusion_reason is null
            then round(chain_user_rating_count / greatest(num_insured, 1), 1)
        end as reviews_per_employee
    from base
),

peers as (
    select
        run_month,
        lead_category,
        count(*)::integer as peer_count,
        median(reviews_per_employee) as category_median_rpe
    from rpe
    where exclusion_reason is null
    group by 1, 2
),

signals as (
    select
        rpe.*,
        p.peer_count,
        p.category_median_rpe,
        case when rpe.exclusion_reason is null
            then round(rpe.reviews_per_employee / nullif(p.category_median_rpe, 0), 1)
        end as rpe_vs_median
    from rpe
    left join peers as p
        on p.run_month = rpe.run_month and p.lead_category = rpe.lead_category
        and rpe.exclusion_reason is null
)

select
    * exclude (reviews_per_employee, peer_count, category_median_rpe, rpe_vs_median),
    reviews_per_employee,
    peer_count,
    category_median_rpe,
    rpe_vs_median,
    case when exclusion_reason is null then
        {{ lead_is_busy('rpe_vs_median', 'peer_count', var('busy_multiple'), var('min_peers')) }}
    end as is_busy,
    case when exclusion_reason is null then
        {{ lead_is_low_wage('avg_wage', var('min_monthly_wage'), var('low_wage_margin')) }}
    end as is_low_wage,
    case when exclusion_reason is null
        then round(avg_wage / {{ var('min_monthly_wage') }}, 2)
    end as wage_vs_minimum
from signals
