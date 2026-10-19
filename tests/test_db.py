import pytest

from src.db import StepStats, ensure_schema, insert_rows, step
from src.run import new_run

RUN = new_run("2026-10")


def tables(con):
    return sorted(
        r[0]
        for r in con.execute(
            "select table_name from information_schema.tables where table_schema='raw'"
        ).fetchall()
    )


def test_ensure_schema_idempotent(con):
    before = tables(con)
    ensure_schema(con)
    assert tables(con) == before
    assert before == ["places", "places_errors", "run_log", "sodra", "web_pages"]


def test_roundtrip_each_table(con):
    rows = {
        "places": {"category": "c", "tile_id": "t", "request_hash": "h", "place_id": "p1",
                   "payload": {"id": "p1", "x": [1]}},
        "places_errors": {"category": "c", "tile_id": "t", "request_hash": "h",
                          "http_status": 500, "error": "boom"},
        "web_pages": {"place_id": "p1", "page_role": "home", "requested_url": "u",
                      "final_url": "u", "http_status": 200, "skip_reason": None,
                      "markdown": "# hi", "links": ["a", "b"], "content_sha256": "s"},
        "sodra": {"source_url": "u", "file_sha256": "s", "data_month": "202601", "code": "1",
                  "jarCode": "2", "name": "n"},
        "run_log": {"run_id": RUN.run_id, "run_month": "2026-10", "step": "x", "status": "ok"},
    }
    for table, row in rows.items():
        assert insert_rows(con, table, [row], RUN) == 1
        assert con.execute(f"select count(*) from raw.{table}").fetchone()[0] == 1
        assert con.execute(f"select run_id from raw.{table}").fetchone()[0] == RUN.run_id
    for table in ("places", "places_errors", "web_pages", "sodra"):
        assert con.execute(f"select retrieved_at from raw.{table}").fetchone()[0] is not None
    assert con.execute("select payload->>'id' from raw.places").fetchone()[0] == "p1"
    assert con.execute("select links->>0 from raw.web_pages").fetchone()[0] == "a"
    assert con.execute('select "jarCode" from raw.sodra').fetchone()[0] == "2"


def test_insert_rows_empty_and_explicit_run_id(con):
    assert insert_rows(con, "places", [], RUN) == 0
    insert_rows(con, "places", [{"run_id": "other", "place_id": "p", "payload": {}}], RUN)
    assert con.execute("select run_id from raw.places").fetchone()[0] == "other"


def run_log(con):
    return con.execute("select status, notes, items_ok, run_month from raw.run_log").fetchall()


def test_step_writes_failed_on_exception(con):
    with pytest.raises(RuntimeError):
        with step(con, RUN, "places"):
            raise RuntimeError("x")
    rows = run_log(con)
    assert len(rows) == 1
    assert rows[0][0] == "failed"
    assert "RuntimeError: x" in rows[0][1]


def test_step_ok_partial_and_capped(con):
    with step(con, RUN, "a") as s:
        assert isinstance(s, StepStats)
        s.items_ok = 3
        s.notes.append("n1")
        s.notes.append("n2")
    with step(con, RUN, "b") as s:
        s.items_failed = 1
    with step(con, RUN, "c") as s:
        s.items_failed = 1
        s.status = "capped"
    got = {
        r[0]: r[1:]
        for r in con.execute("select step, status, notes, run_month from raw.run_log").fetchall()
    }
    assert got["a"] == ("ok", "n1; n2", "2026-10")
    assert got["b"][0] == "partial"
    assert got["c"][0] == "capped"
    assert got["b"][1] is None
    row = con.execute(
        "select started_at, finished_at from raw.run_log where step='a'"
    ).fetchone()
    assert row[0] <= row[1]


def test_sodra_leading_zero_kept(con):
    insert_rows(con, "sodra", [{"code": "0012", "jarCode": "00034"}], RUN)
    assert con.execute('select code, "jarCode" from raw.sodra').fetchone() == ("0012", "00034")
