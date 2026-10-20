{# One row per standalone 9-digit number on a fetched page. Occurrences come from
   splitting the markdown on the code regex, so an earlier code never hides inside a
   later code's window. #}
with pages as (
    select
        run_month,
        place_id,
        page_role,
        final_url,
        regexp_extract_all(markdown, '\b\d{9}\b') as codes,
        regexp_split_to_array(markdown, '\b\d{9}\b') as segs
    from {{ ref('stg_web_pages') }}
    where is_fetched and markdown is not null
),

occurrences as (
    select
        run_month,
        place_id,
        page_role,
        final_url,
        codes,
        segs,
        unnest(generate_series(1, len(codes))) as hit_index
    from pages
),

hits as (
    select
        run_month,
        place_id,
        page_role,
        final_url,
        hit_index,
        codes[hit_index] as jar_code,
        right(segs[hit_index], 40) as before_text,
        left(segs[hit_index + 1], 40) as after_text
    from occurrences
)

select
    h.run_month,
    h.place_id,
    h.page_role,
    h.final_url,
    h.hit_index,
    h.jar_code,
    h.before_text,
    h.after_text,
    regexp_matches(
        h.before_text,
        '(?i)(įmonės|imones)\s+kodas|\bkodas\b|įm\.\s*k\.|company\s+code|juridinio asmens kodas'
    ) as is_labelled,
    exists (
        select 1
        from {{ ref('stg_sodra') }} as s
        where s.run_id = rp.sodra_run_id
          and s.jar_code = h.jar_code
    ) as in_sodra
from hits as h
left join {{ ref('int_run_pairs') }} as rp on rp.run_month = h.run_month
