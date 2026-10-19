import json

import httpx
import pytest

from src.extract.places import _note_int, main, places_summary, run_places
from src.run import new_run
from tests.replay import replay_transport
from tests.test_places import config, make_client, partial_page

RUN = new_run("2026-10")


def sql_counts(con, run_id):
    return {
        r[0]: {"places": r[1], "unique_places": r[2], "with_website": r[3]}
        for r in con.execute(
            "select category, count(*), count(distinct place_id),"
            " count(*) filter (where payload->>'websiteUri' is not null)"
            " from raw.places where run_id = ? group by 1 order by 1", [run_id]).fetchall()
    }


def test_note_int():
    notes = "a:saturated_tiles=3; a:coverage_gaps=2; b:saturated_tiles=0"
    assert _note_int(notes, "a", "saturated_tiles") == 3
    assert _note_int(notes, "a", "coverage_gaps") == 2
    assert _note_int(notes, "b", "saturated_tiles") == 0
    assert _note_int(notes, "b", "coverage_gaps") == 0  # missing -> 0
    assert _note_int(None, "a", "coverage_gaps") == 0


def test_summary_matches_sql(con, tmp_path):
    client, _ = make_client(tmp_path, partial_page)
    cfg = config({"a": ["cafe"], "b": ["restaurant"]})
    run_places(con, client, cfg, RUN)
    summary = places_summary(con, RUN.run_id)
    sql = sql_counts(con, RUN.run_id)
    assert [s["category"] for s in summary] == ["a", "b"]
    for s in summary:
        exp = sql[s["category"]]
        assert {k: s[k] for k in exp} == exp
        assert exp["places"] > 0
        assert s["saturated_tiles"] == 0 and s["coverage_gaps"] == 0


def test_main_unknown_category_exits_2(con, tmp_path, capsys):
    client, calls = make_client(tmp_path, partial_page)
    with pytest.raises(SystemExit) as e:
        main(["--category", "nope"], client=client, con=con, config=config({"a": ["cafe"]}))
    assert e.value.code == 2 and not calls
    assert "nope" in capsys.readouterr().err


def test_main_failed_exits_1(con, tmp_path):
    client, _ = make_client(tmp_path, lambda body: httpx.Response(403, json={"error": {}}))
    code = main([], client=client, con=con, config=config({"a": ["cafe"]}))
    assert code == 1
    assert con.execute("select status from raw.run_log").fetchone()[0] == "failed"


def test_main_run_id_reused(con, tmp_path, capsys):
    client, _ = make_client(tmp_path, partial_page)
    code = main(["--run-id", "2026-09_20260901T000000", "--category", "a"],
                client=client, con=con, config=config({"a": ["cafe"], "b": ["cafe"]}))
    assert code == 0
    assert con.execute("select run_id, run_month from raw.run_log").fetchall() == [
        ("2026-09_20260901T000000", "2026-09")]
    out = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert [o["category"] for o in out if o["event"] == "places_summary"] == ["a"]


@pytest.mark.recorded
def test_main_end_to_end_recorded(con, capsys):
    from src.cache import SourceClient, TokenBucket

    bbox = {"south": 54.679, "west": 25.276, "north": 54.688, "east": 25.291}
    cfg = config({"cafes_restaurants": ["cafe", "restaurant"]}, tile_km=1.0, min_tile_km=1.0)
    cfg.area.bbox = type(cfg.area.bbox)(**bbox)
    client = SourceClient(
        "places", None, TokenBucket(1000, sleep=lambda s: None), None,
        {"X-Goog-Api-Key": "k"}, transport=replay_transport("places"), sleep=lambda s: None)
    code = main([], client=client, con=con, config=cfg)
    assert code == 0
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    printed = [e for e in events if e["event"] == "places_summary"]
    assert len(printed) == 1
    run_id = con.execute("select run_id from raw.run_log").fetchone()[0]
    exp = sql_counts(con, run_id)["cafes_restaurants"]
    assert {k: printed[0][k] for k in exp} == exp
    assert printed[0]["saturated_tiles"] == 1 and printed[0]["coverage_gaps"] == 1
