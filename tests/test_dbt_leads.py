from src.db import connect
from src.run import new_run
from tests.dbt_harness import build, load_lead_demo, rows


def test_end_to_end_tiers(tmp_path):
    db = tmp_path / "t.duckdb"
    con = connect(db)
    load_lead_demo(con, new_run("2026-10"))
    con.close()
    build(db)
    assert rows(db, """select jar_code, tier, exclusion_reason, lead_rank, priority_rank
                       from marts.mart_leads order by jar_code""") == [
        ("100000001", "clear", None, 1, 3),
        ("100000002", "clear", None, 2, 4),
        ("100000003", "clear", None, 3, 5),
        ("100000004", "clear", None, 4, 6),
        ("100000005", "flag", None, 1, 1),
        ("100000006", "watch", None, 1, 2),
        ("100000007", "excluded", "too_few_reviews", None, None),
    ]
