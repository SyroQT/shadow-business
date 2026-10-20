from datetime import UTC, datetime

from src.db import connect
from src.run import new_run
from tests.dbt_harness import build, load, page, place, rows

CAT = "cafes_restaurants"


def _run(month, day):
    return new_run(month, now=datetime(*map(int, month.split("-")), day, tzinfo=UTC))


def _db(tmp_path, loads):
    db = tmp_path / "t.duckdb"
    con = connect(db)
    for run, kwargs in loads:
        load(con, run, **kwargs)
    con.close()
    build(db)
    return db


def test_places_unpacked(tmp_path):
    full = place("p1", CAT, "Kavinė", website="https://a.lt", user_rating_count=42, rating=4.5)
    bare = place("p2", CAT, "Be svetainės")
    db = _db(tmp_path, [(_run("2026-10", 1), {"places": [full, bare]})])
    r = rows(db, """select name, types, lat, lng, user_rating_count, rating, website_uri,
                    run_month, primary_type, address, business_status, google_maps_uri
                    from staging.stg_places where place_id = 'p1'""")[0]
    assert r[0] == "Kavinė"
    assert r[1] == ["cafe", "food", "establishment"]
    assert (r[2], r[3]) == (54.687, 25.279)
    assert r[4] == 42 and isinstance(r[4], int)
    assert r[5] == 4.5 and r[6] == "https://a.lt" and r[7] == "2026-10"
    assert r[8] == "cafe" and r[10] == "OPERATIONAL" and r[11].startswith("https://maps")
    assert rows(db, """select rating, user_rating_count, website_uri
                       from staging.stg_places where place_id = 'p2'""") == [(None, None, None)]


def test_latest_run_per_month_wins(tmp_path):
    db = _db(tmp_path, [
        (_run("2026-10", 1), {"places": [place("old", CAT, "Old")]}),
        (_run("2026-10", 2), {"places": [place("new", CAT, "New")]}),
    ])
    assert rows(db, "select place_id from staging.stg_places") == [("new",)]


def test_failed_run_is_skipped(tmp_path):
    db = _db(tmp_path, [
        (_run("2026-10", 1), {"places": [place("good", CAT, "Good")]}),
        (_run("2026-10", 2), {"places": [place("bad", CAT, "Bad")], "steps": {"places": "failed"}}),
    ])
    assert rows(db, "select place_id from staging.stg_places") == [("good",)]


def test_months_kept_side_by_side(tmp_path):
    db = _db(tmp_path, [
        (_run("2026-09", 1), {"places": [place("a", CAT, "A")]}),
        (_run("2026-10", 1), {"places": [place("b", CAT, "B")]}),
    ])
    assert rows(db, "select run_month, place_id from staging.stg_places order by 1") == [
        ("2026-09", "a"), ("2026-10", "b")]


def test_place_in_two_categories_kept(tmp_path):
    db = _db(tmp_path, [(_run("2026-10", 1), {"places": [
        place("p", CAT, "P"), place("p", "hair_beauty", "P")]})])
    assert rows(db, "select category from staging.stg_places order by 1") == [
        (CAT,), ("hair_beauty",)]


def test_web_pages_follow_places_run(tmp_path):
    r1, r2 = _run("2026-10", 1), _run("2026-10", 2)
    db = _db(tmp_path, [
        (r1, {"places": [place("old", CAT, "Old")], "pages": [page("old", "home", "x")]}),
        (r2, {"places": [place("a", CAT, "A"), place("b", CAT, "B")],
              "pages": [
                  page("a", "home", "# hi"),
                  page("a", "legal_candidate", "legal"),
                  page("b", "home", None, skip_reason="no_website"),
              ]}),
    ])
    got = rows(db, """select place_id, page_role, is_fetched, run_month
                      from staging.stg_web_pages order by 1, 2""")
    assert got == [("a", "home", True, "2026-10"), ("a", "legal_candidate", True, "2026-10"),
                   ("b", "home", False, "2026-10")]
