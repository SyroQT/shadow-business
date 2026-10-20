{# One row per (run_month, jar_code): the matched places of int_places_matched rolled up
   to the company. Leads spec section 1. #}
with matched as (
    select
        *,
        case match_confidence when 'high' then 3 when 'medium' then 2 when 'low' then 1 end as confidence_rank
    from {{ ref('int_places_matched') }}
    where match_status = 'matched'
),

category_counts as (
    select run_month, jar_code, category, count(*) as n
    from matched, unnest(categories) as t(category)
    group by 1, 2, 3
),

lead_category as (
    select
        run_month,
        jar_code,
        arg_min(category, (-n, category)) as lead_category
    from category_counts
    group by 1, 2
),

best_place as (
    select
        run_month,
        jar_code,
        match_confidence as best_match_confidence,
        match_confidence_reasons as best_match_confidence_reasons,
        code_source_url,
        code_context
    from matched
    qualify row_number() over (
        partition by run_month, jar_code order by confidence_rank desc, place_id
    ) = 1
),

company as (
    select
        run_month,
        jar_code,
        max(company_name) as company_name,
        max(evrk_code) as evrk_code,
        max(evrk_name) as evrk_name,
        max(sodra_month) as sodra_month,
        max(num_insured) as num_insured,
        max(avg_wage) as avg_wage,
        max(tax) as tax,
        max(sodra_run_id) as sodra_run_id,
        max(sodra_source_url) as sodra_source_url,
        max(sodra_file_sha256) as sodra_file_sha256,
        max(places_run_id) as places_run_id,
        count(*)::integer as n_places,
        sum(coalesce(user_rating_count, 0))::bigint as chain_user_rating_count,
        coalesce(bool_or(business_status = 'OPERATIONAL'), false) as any_operational,
        list(
            {
                'place_id': place_id,
                'name': name,
                'address': address,
                'user_rating_count': user_rating_count,
                'google_maps_uri': google_maps_uri
            }
            order by user_rating_count desc nulls last, place_id
        ) as places
    from matched
    group by 1, 2
)

select
    c.run_month,
    c.jar_code,
    c.company_name,
    c.evrk_code,
    c.evrk_name,
    c.sodra_month,
    c.num_insured,
    c.avg_wage,
    c.tax,
    c.sodra_run_id,
    c.sodra_source_url,
    c.sodra_file_sha256,
    c.places_run_id,
    l.lead_category,
    c.n_places,
    c.chain_user_rating_count,
    b.best_match_confidence,
    b.best_match_confidence_reasons,
    c.any_operational,
    c.places,
    b.code_source_url,
    b.code_context
from company as c
join lead_category as l using (run_month, jar_code)
join best_place as b using (run_month, jar_code)
