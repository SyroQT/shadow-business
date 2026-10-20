with selected as (
    {{ selected_runs('places') }}
),

unpacked as (
    select
        s.run_month,
        p.place_id,
        p.category,
        p.payload ->> '$.displayName.text' as name,
        p.payload ->> '$.primaryType' as primary_type,
        cast(from_json(p.payload -> '$.types', '["VARCHAR"]') as varchar[]) as types,
        p.payload ->> '$.formattedAddress' as address,
        try_cast(p.payload ->> '$.location.latitude' as double) as lat,
        try_cast(p.payload ->> '$.location.longitude' as double) as lng,
        try_cast(p.payload ->> '$.rating' as double) as rating,
        try_cast(p.payload ->> '$.userRatingCount' as integer) as user_rating_count,
        p.payload ->> '$.websiteUri' as website_uri,
        p.payload ->> '$.businessStatus' as business_status,
        p.payload ->> '$.googleMapsUri' as google_maps_uri,
        p.run_id,
        p.retrieved_at
    from {{ source('raw', 'places') }} as p
    inner join selected as s on s.run_id = p.run_id
),

deduped as (
    select *,
        row_number() over (
            partition by run_month, place_id, category
            order by retrieved_at
        ) as rn
    from unpacked
)

select
    run_month, place_id, category, name, primary_type, types, address,
    lat, lng, rating, user_rating_count, website_uri, business_status,
    google_maps_uri, run_id, retrieved_at
from deduped
where rn = 1
