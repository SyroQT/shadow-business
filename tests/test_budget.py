from datetime import datetime

from src.budget import format_caps, plan_caps, used_this_month
from src.db import connect
from src.settings import Settings

NOW = datetime(2026, 10, 20, 12)


def _log(con, run_id, step, started_at, api_requests):
    con.execute("insert into raw.run_log (run_id, run_month, step, started_at, api_requests) "
                "values (?, ?, ?, ?, ?)", [run_id, started_at.strftime("%Y-%m"), step,
                                           started_at, api_requests])


def settings(**kw) -> Settings:
    return Settings(_env_file=None, **kw)


def test_used_this_month_counts_current_month_only(tmp_path):
    db = tmp_path / "t.duckdb"
    con = connect(db)
    _log(con, "a", "places", datetime(2026, 9, 30, 23, 59), 500)
    _log(con, "b", "places", datetime(2026, 10, 1), 11)
    _log(con, "b", "crawl", datetime(2026, 10, 1, 1), 247)
    _log(con, "c", "places", datetime(2026, 10, 19), 4)
    _log(con, "c", "sodra", datetime(2026, 10, 19), 99)
    _log(con, "d", "places", datetime(2026, 11, 1), 7)
    con.close()
    assert used_this_month(db, NOW) == {"places": 15, "crawl": 247}


def test_used_this_month_december_rollover(tmp_path):
    db = tmp_path / "t.duckdb"
    con = connect(db)
    _log(con, "a", "places", datetime(2026, 12, 31), 3)
    _log(con, "b", "places", datetime(2027, 1, 1), 5)
    con.close()
    assert used_this_month(db, datetime(2026, 12, 15)) == {"places": 3, "crawl": 0}


def test_used_this_month_missing_db(tmp_path):
    db = tmp_path / "none.duckdb"
    assert used_this_month(db, NOW) == {"places": 0, "crawl": 0}
    assert not db.exists()


def test_plan_caps_min_of_max_and_remaining():
    c = plan_caps(settings(max_places_requests=1000, max_firecrawl_pages=600),
                  {"places": 950, "crawl": 0})
    assert (c.places_remaining, c.places_cap) == (50, 50)
    assert (c.firecrawl_remaining, c.firecrawl_cap) == (1000, 600)
    assert c.exhausted == []


def test_plan_caps_floor_zero():
    c = plan_caps(settings(), {"places": 1200, "crawl": 1000})
    assert (c.places_remaining, c.places_cap) == (0, 0)
    assert c.exhausted == ["places", "firecrawl"]


def test_format_caps():
    c = plan_caps(settings(max_firecrawl_pages=600), {"places": 11, "crawl": 247})
    assert format_caps(c) == [
        "places: used 11 of 1000 this month, remaining 989, run cap 989",
        "firecrawl: used 247 of 1000 this month, remaining 753, run cap 600",
    ]
