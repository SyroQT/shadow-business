import json
import re
from pathlib import Path

import duckdb
import pytest

from src.db import connect, insert_rows
from src.run import new_run
from src.snapshot import SnapshotError, write_snapshot
from tests.dbt_harness import page
from tests.snapshot_fixture import RUN, raw_lead_db

ROOT = Path(__file__).resolve().parent.parent
KEY_PATTERNS = re.compile(r"AIza[0-9A-Za-z_-]{20,}|fc-[0-9a-f]{20,}|Bearer ")


@pytest.fixture
def db(tmp_path):
    return raw_lead_db(tmp_path / "t.duckdb")


def _codes(out):
    return sorted(r[0] for r in duckdb.sql(f"select jarCode from '{out / 'sodra.parquet'}'").fetchall())


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


def test_snapshot_sodra_only_page_codes(db, tmp_path):
    out = write_snapshot(db, RUN.run_id, tmp_path / "s")
    assert _codes(out) == [f"10000000{i}" for i in range(1, 8)]  # not 999999999


def test_snapshot_keeps_earlier_month_sodra(db, tmp_path):
    """A November run without its own Sodra pairs with October's Sodra (int_run_pairs)."""
    nov = new_run("2026-11")
    con = connect(db)
    for t in ("places", "web_pages"):
        con.execute(f"insert into raw.{t} select * replace (? as run_id) from raw.{t} "
                    "where run_id = ?", [nov.run_id, RUN.run_id])
    insert_rows(con, "run_log", [
        {"run_month": "2026-11", "step": step, "started_at": nov.started_at.replace(tzinfo=None),
         "status": "ok", "api_requests": 0}
        for step in ("places", "crawl")], nov)
    con.close()
    out = write_snapshot(db, nov.run_id, tmp_path / "s")
    assert len(_codes(out)) == 7
    steps = duckdb.sql(f"select step, run_month from '{out / 'run_log.parquet'}' "
                       "order by all").fetchall()
    assert steps == [("crawl", "2026-11"), ("places", "2026-11"), ("sodra", "2026-10")]


def test_snapshot_refuses_non_empty(db, tmp_path):
    out = tmp_path / "s"
    out.mkdir()
    (out / "x").write_text("x")
    with pytest.raises(SnapshotError, match="not empty"):
        write_snapshot(db, None, out)
    write_snapshot(db, None, out, force=True)
    assert (out / "manifest.json").exists()


def test_snapshot_missing_db(tmp_path):
    with pytest.raises(SnapshotError, match="no database"):
        write_snapshot(tmp_path / "none.duckdb", None, tmp_path / "s")


def test_snapshot_redacts_page_keys(db, tmp_path):
    con = connect(db)
    fake = "AIza" + "x" * 35
    insert_rows(con, "web_pages", [page("demo1", "legal_candidate",
                                        f"![map](https://maps.googleapis.com/x?key={fake})")], RUN)
    con.close()
    out = write_snapshot(db, None, tmp_path / "s")
    text = repr(duckdb.sql(f"select * from '{out / 'web_pages.parquet'}'").fetchall())
    assert fake not in text and "key=[redacted-key]" in text


@pytest.mark.parametrize("f", sorted((ROOT / "snapshots").glob("*/*")), ids=lambda f: f.name)
def test_committed_snapshot_has_no_keys(f):
    text = (f.read_text() if f.suffix == ".json"
            else repr(duckdb.sql(f"select * from '{f}'").fetchall()))
    assert not KEY_PATTERNS.search(text)
