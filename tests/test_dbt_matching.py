from datetime import UTC, datetime

from src.db import connect
from src.run import new_run
from tests.dbt_harness import build, load, page, place, rows, sodra

CAT = "cafes_restaurants"


def _run(month, day=1):
    return new_run(month, now=datetime(*map(int, month.split("-")), day, tzinfo=UTC))


def _db(tmp_path, loads):
    db = tmp_path / "t.duckdb"
    con = connect(db)
    for run, kwargs in loads:
        load(con, run, **kwargs)
    con.close()
    build(db)
    return db


def test_run_pairs_fallback(tmp_path):
    sep, octo = _run("2026-09"), _run("2026-10")
    db = _db(tmp_path, [
        (sep, {"places": [place("p1", CAT, "A")], "sodra": [sodra("111111111")],
               "steps": {"places": "ok", "sodra": "ok"}}),
        (octo, {"places": [place("p1", CAT, "A")]}),
    ])
    assert rows(db, """select run_month, places_run_id, sodra_run_id
                       from intermediate.int_run_pairs order by 1""") == [
        ("2026-09", sep.run_id, sep.run_id),
        ("2026-10", octo.run_id, sep.run_id),
    ]


def test_run_pairs_greatest_earlier_and_ignores_later(tmp_path):
    aug, sep, octo, nov = (_run(m) for m in ("2026-08", "2026-09", "2026-10", "2026-11"))
    steps = {"sodra": "ok"}
    db = _db(tmp_path, [
        (aug, {"sodra": [sodra("111111111")], "steps": steps}),
        (sep, {"sodra": [sodra("111111111")], "steps": steps}),
        (octo, {"places": [place("p1", CAT, "A")], "steps": {"places": "ok"}}),
        (nov, {"sodra": [sodra("111111111")], "steps": steps}),
    ])
    assert rows(db, """select run_month, sodra_run_id
                       from intermediate.int_run_pairs order by 1""") == [
        ("2026-10", sep.run_id)]


def test_run_pairs_no_sodra(tmp_path):
    sep = _run("2026-09")
    db = _db(tmp_path, [(sep, {"places": [place("p1", CAT, "A")]})])
    assert rows(db, "select run_month, sodra_run_id from intermediate.int_run_pairs") == [
        ("2026-09", None)]


def _matched(db, sql_where=""):
    return rows(db, f"""select run_month, place_id, crawl_status, match_status, jar_code,
                        n_candidate_codes, sodra_run_id is not null
                        from intermediate.int_places_matched {sql_where} order by 1, 2""")


def test_place_without_pages(tmp_path):
    octo = _run("2026-10")
    db = _db(tmp_path, [(octo, {"places": [place("p1", CAT, "A")], "sodra": [sodra("111111111")],
                                "steps": {"places": "ok", "sodra": "ok"}})])
    assert _matched(db) == [("2026-10", "p1", "not_crawled", "not_fetched", None, 0, True)]


def test_month_without_sodra(tmp_path):
    sep = _run("2026-09")
    db = _db(tmp_path, [(sep, {
        "places": [place("p1", CAT, "A", website="https://x.lt")],
        "pages": [page("p1", "home", "Įmonės kodas: 111111111")]})])
    assert rows(db, """select match_status, jar_code, sodra_run_id, places_run_id is not null
                       from intermediate.int_places_matched""") == [
        ("code_not_in_sodra", None, None, True)]


def test_two_months_independent(tmp_path):
    sep, octo = _run("2026-09"), _run("2026-10")
    steps = {"places": "ok", "sodra": "ok"}
    db = _db(tmp_path, [
        (sep, {"places": [place("p1", CAT, "A", website="https://x.lt")],
               "pages": [page("p1", "home", "Welcome")],
               "sodra": [sodra("111111111")], "steps": steps}),
        (octo, {"places": [place("p1", CAT, "A", website="https://x.lt")],
                "pages": [page("p1", "home", "Įmonės kodas: 111111111")],
                "sodra": [sodra("111111111")], "steps": steps}),
    ])
    assert [(r[0], r[3], r[4]) for r in _matched(db)] == [
        ("2026-09", "no_code_found", None), ("2026-10", "matched", "111111111")]
