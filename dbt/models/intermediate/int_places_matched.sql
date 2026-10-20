{# One row per (run_month, place_id): every place of each month's selected run.
   Spec section 4.2 steps 1-4. match_confidence and chain columns come later. #}
with places as (
    select
        run_month,
        place_id,
        list_sort(list_distinct(list(category))) as categories,
        max(name) as name,
        max(primary_type) as primary_type,
        max(address) as address,
        max(lat) as lat,
        max(lng) as lng,
        max(rating) as rating,
        max(user_rating_count) as user_rating_count,
        max(website_uri) as website_uri,
        max(business_status) as business_status,
        max(google_maps_uri) as google_maps_uri
    from {{ ref('stg_places') }}
    group by run_month, place_id
),

crawl as (
    select
        run_month,
        place_id,
        count(*) filter (where is_fetched)::integer as pages_fetched,
        bool_or(page_role = 'home' and is_fetched) as home_fetched,
        max(skip_reason) filter (where page_role = 'home') as home_skip_reason
    from {{ ref('stg_web_pages') }}
    group by run_month, place_id
),

candidates as (
    select
        *,
        count(*) over (partition by run_month, place_id)::integer as n_candidate_codes,
        row_number() over (
            partition by run_month, place_id
            order by is_labelled desc, n_occurrences desc,
                list_contains(found_on_roles, 'legal_candidate') desc, jar_code
        ) as pick
    from {{ ref('int_page_company_codes') }}
),

unmatched_places as (
    select distinct run_month, place_id from {{ ref('int_page_company_codes_unmatched') }}
),

latest_sodra as (
    select
        *,
        row_number() over (partition by run_id, jar_code order by month desc) as recency
    from {{ ref('stg_sodra') }}
),

evrk_prefixes as (
    select category, evrk_prefix from {{ ref('category_evrk') }}
),

staged as (
    select
        p.*,
        case
            when c.place_id is null then 'not_crawled'
            when c.home_fetched then 'fetched'
            else coalesce(c.home_skip_reason, 'error')
        end as crawl_status,
        coalesce(c.pages_fetched, 0) as pages_fetched,
        r.places_run_id,
        r.sodra_run_id
    from places as p
    left join crawl as c using (run_month, place_id)
    left join {{ ref('int_run_pairs') }} as r using (run_month)
)

select
    s.run_month,
    s.place_id,
    s.name,
    s.categories,
    s.primary_type,
    s.address,
    s.lat,
    s.lng,
    s.rating,
    s.user_rating_count,
    s.website_uri,
    s.business_status,
    s.google_maps_uri,
    s.crawl_status,
    s.pages_fetched,
    case
        when s.crawl_status <> 'fetched' then 'not_fetched'
        when k.jar_code is not null then 'matched'
        when u.place_id is not null then 'code_not_in_sodra'
        else 'no_code_found'
    end as match_status,
    case when s.crawl_status = 'fetched' then k.jar_code end as jar_code,
    case when s.crawl_status = 'fetched' then k.is_labelled end as code_is_labelled,
    coalesce(k.n_candidate_codes, 0) as n_candidate_codes,
    case when s.crawl_status = 'fetched' then k.first_url end as code_source_url,
    case when s.crawl_status = 'fetched' then k.code_context end as code_context,
    d.company_name,
    d.municipality,
    d.evrk_code,
    d.evrk_name,
    d.month as sodra_month,
    d.num_insured,
    d.avg_wage,
    d.tax,
    case
        when d.jar_code is null then null
        else exists (
            select 1
            from evrk_prefixes as e
            where list_contains(s.categories, e.category)
              and starts_with(d.evrk_code, e.evrk_prefix)
        )
    end as evrk_matches_category,
    s.places_run_id,
    s.sodra_run_id,
    d.source_url as sodra_source_url,
    d.file_sha256 as sodra_file_sha256
from staged as s
left join candidates as k
    on k.run_month = s.run_month and k.place_id = s.place_id and k.pick = 1
    and s.crawl_status = 'fetched'
left join unmatched_places as u
    on u.run_month = s.run_month and u.place_id = s.place_id
left join latest_sodra as d
    on d.run_id = s.sodra_run_id and d.jar_code = k.jar_code and d.recency = 1
    and s.crawl_status = 'fetched'
