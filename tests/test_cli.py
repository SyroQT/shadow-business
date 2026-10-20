import os
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
    monkeypatch.setenv("MAX_PLACES_REQUESTS", "1000")
    monkeypatch.setenv("MAX_FIRECRAWL_PAGES", "600")
    calls = {"pipeline": 0, "snapshot": []}

    def fake_pipeline():
        calls["pipeline"] += 1
        return calls.get("pipeline_code", 0)

    monkeypatch.setattr(pipeline, "main", fake_pipeline)
    monkeypatch.setattr(cli, "cmd_snapshot", lambda *a: calls["snapshot"].append(a) or 0)
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
    assert calls["pipeline"] == 0
    assert capsys.readouterr().err.strip() == \
        "full: places free tier used up this month (1000 of 1000)"


def test_full_dry_run_no_pipeline(env, capsys):
    db, calls = env
    _spend(db, 11, 247)
    assert cli.main(["full", "--dry-run"]) == 0
    assert calls["pipeline"] == 0
    assert capsys.readouterr().out.splitlines() == [
        "places: used 11 of 1000 this month, remaining 989, run cap 989",
        "firecrawl: used 247 of 1000 this month, remaining 753, run cap 600",
    ]


def test_full_sets_caps_env_then_snapshots(env):
    db, calls = env
    _spend(db, 950, 700)
    assert cli.main(["full"]) == 0
    assert (os.environ["MAX_PLACES_REQUESTS"], os.environ["MAX_FIRECRAWL_PAGES"]) == ("50", "300")
    assert calls["pipeline"] == 1
    assert calls["snapshot"] == [(None, None, False)]


def test_full_pipeline_failure_skips_snapshot(env):
    _, calls = env
    calls["pipeline_code"] = 1
    assert cli.main(["full"]) == 1
    assert calls["snapshot"] == []


def test_unknown_command_exits_nonzero():
    with pytest.raises(SystemExit) as exc:
        cli.main(["nope"])
    assert exc.value.code == 2
