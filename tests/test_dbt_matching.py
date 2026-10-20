from datetime import UTC, datetime

from src.db import connect
from src.run import new_run
from tests.dbt_harness import build, load, place, rows, sodra

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


def test_run_pairs_no_sodra(tmp_path):
    sep = _run("2026-09")
    db = _db(tmp_path, [(sep, {"places": [place("p1", CAT, "A")]})])
    assert rows(db, "select run_month, sodra_run_id from intermediate.int_run_pairs") == [
        ("2026-09", None)]
