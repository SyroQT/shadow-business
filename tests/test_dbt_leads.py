from src.db import connect
from src.run import new_run
from src.transform import run_dbt
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


def test_min_wage_follows_sodra_year(tmp_path):
    db = tmp_path / "t.duckdb"
    con = connect(db)
    load_lead_demo(con, new_run("2026-01"), sodra_month="202512")
    con.close()
    build(db)
    [(wage, ratio, reason)] = rows(db, """select min_monthly_wage, wage_vs_minimum, lead_reason
                                          from marts.mart_leads where jar_code = '100000005'""")
    assert wage == 1038
    assert float(ratio) == 0.96
    assert "below the minimum wage (€1038)." in reason


def test_min_wage_missing_year_fails_build(tmp_path):
    db = tmp_path / "t.duckdb"
    con = connect(db)
    load_lead_demo(con, new_run("2027-10"), sodra_month="202708")
    con.close()
    res = run_dbt(["build"], db)
    assert res.returncode != 0
    assert "not_null_int_lead_signals_min_monthly_wage" in res.stdout
