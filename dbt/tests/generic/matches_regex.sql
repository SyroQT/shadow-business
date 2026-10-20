{% test matches_regex(model, column_name, pattern) %}
select {{ column_name }}
from {{ model }}
where {{ column_name }} is not null
  and not regexp_matches({{ column_name }}, '{{ pattern }}')
{% endtest %}
