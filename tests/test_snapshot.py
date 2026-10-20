import json
import re

import duckdb
import pytest

from src import cli
from src.db import connect
from src.settings import Settings
from src.snapshot import SnapshotError, write_snapshot
from tests.dbt_harness import load_lead_demo
from tests.snapshot_fixture import RUN, built_lead_db

KEY_PATTERNS = re.compile(r"AIza[0-9A-Za-z_-]{20,}|fc-[0-9a-f]{20,}|Bearer ")


@pytest.fixture(scope="module")
def db(tmp_path_factory):
    return built_lead_db(tmp_path_factory.mktemp("snap") / "t.duckdb")


def test_snapshot_files_and_manifest(db, tmp_path):
    out = write_snapshot(db, None, tmp_path / "s")
    names = sorted(p.name for p in out.iterdir())
    assert names == ["manifest.json", "places.parquet", "places_errors.parquet",
                     "run_log.parquet", "sodra.parquet", "web_pages.parquet"]
    m = json.loads((out / "manifest.json").read_text())
    assert (m["run_id"], m["run_month"]) == (RUN.run_id, "2026-10")
    assert m["rows"] == {"places.parquet": 7, "places_errors.parquet": 0,
                         "web_pages.parquet": 7, "sodra.parquet": 7, "run_log.parquet": 3}
    assert m["sodra_source_url"] == "file:s.zip"


def test_snapshot_sodra_filtered(db, tmp_path):
    out = write_snapshot(db, RUN.run_id, tmp_path / "s")
    codes = duckdb.sql(f"select jarCode from '{out / 'sodra.parquet'}'").fetchall()
    assert ("999999999",) not in codes and len(codes) == 7


def test_snapshot_refuses_non_empty(db, tmp_path):
    out = tmp_path / "s"
    out.mkdir()
    (out / "x").write_text("x")
    with pytest.raises(SnapshotError, match="not empty"):
        write_snapshot(db, None, out)
    write_snapshot(db, None, out, force=True)
    assert (out / "manifest.json").exists()


def test_snapshot_requires_dbt_build(tmp_path, monkeypatch, capsys):
    raw_only = tmp_path / "nobuild.duckdb"
    con = connect(raw_only)
    load_lead_demo(con, RUN)
    con.close()
    monkeypatch.setattr(cli, "load_settings_or_exit",
                        lambda: Settings(_env_file=None, db_path=raw_only))
    assert cli.main(["snapshot", "--out", str(tmp_path / "s")]) == 1
    err = capsys.readouterr().err
    assert err.count("\n") == 1 and "run dbt build first" in err


def test_snapshot_missing_db(tmp_path):
    with pytest.raises(SnapshotError, match="no database"):
        write_snapshot(tmp_path / "none.duckdb", None, tmp_path / "s")


def test_snapshot_no_key_patterns(db, tmp_path):
    out = write_snapshot(db, None, tmp_path / "s")
    for f in out.glob("*.parquet"):
        text = repr(duckdb.sql(f"select * from '{f}'").fetchall())
        assert not KEY_PATTERNS.search(text), f.name
    assert not KEY_PATTERNS.search((out / "manifest.json").read_text())
