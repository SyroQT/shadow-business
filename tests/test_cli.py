from datetime import datetime

import pytest

from src import cli, pipeline
from src.db import connect
from src.settings import Settings


@pytest.fixture
def env(tmp_path, monkeypatch):
    db = tmp_path / "t.duckdb"
    monkeypatch.setattr(cli, "load_settings_or_exit",
                        lambda: Settings(_env_file=None, db_path=db, max_firecrawl_pages=600))
    calls = {"pipeline": [], "snapshot": []}

    def fake_run(settings):
        calls["pipeline"].append(settings)
        return calls.get("pipeline_code", 0), "2026-10_run"

    monkeypatch.setattr(pipeline, "run", fake_run)
    monkeypatch.setattr(cli, "_snapshot", lambda *a: calls["snapshot"].append(a) or 0)
    return db, calls


def _spend(db, places, crawl):
    con = connect(db)
    now = datetime.now()
    for step, n in (("places", places), ("crawl", crawl)):
        con.execute("insert into raw.run_log (run_id, step, started_at, api_requests) "
                    "values ('r', ?, ?, ?)", [step, now, n])
    con.close()


def test_full_exhausted_exits_2_before_pipeline(env, capsys):
    db, calls = env
    _spend(db, 1000, 10)
    assert cli.main(["full"]) == 2
    assert calls["pipeline"] == []
    assert capsys.readouterr().err.strip() == \
        "full: places free tier used up this month (1000 of 1000)"


def test_full_dry_run_no_pipeline(env, capsys):
    db, calls = env
    _spend(db, 11, 247)
    assert cli.main(["full", "--dry-run"]) == 0
    assert calls["pipeline"] == []
    assert capsys.readouterr().out.splitlines() == [
        "places: used 11 of 1000 this month, remaining 989, run cap 989",
        "firecrawl: used 247 of 1000 this month, remaining 753, run cap 600",
    ]


def test_full_passes_caps_then_snapshots_its_run(env):
    db, calls = env
    _spend(db, 950, 700)
    assert cli.main(["full"]) == 0
    [s] = calls["pipeline"]
    assert (s.max_places_requests, s.max_firecrawl_pages) == (50, 300)
    assert calls["snapshot"] == [(db, "2026-10_run", None, False)]


def test_full_pipeline_failure_skips_snapshot(env):
    _, calls = env
    calls["pipeline_code"] = 1
    assert cli.main(["full"]) == 1
    assert calls["snapshot"] == []


def test_unknown_command_exits_nonzero():
    with pytest.raises(SystemExit) as exc:
        cli.main(["nope"])
    assert exc.value.code == 2
