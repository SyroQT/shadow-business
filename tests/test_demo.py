import duckdb
import httpx
import pytest

from src import cli
from src.demo import newest_snapshot, run_demo
from src.snapshot import write_snapshot
from tests.dbt_harness import rows
from tests.snapshot_fixture import built_lead_db

MART = "select * exclude (sodra_source_url) from marts.mart_leads order by jar_code"


@pytest.fixture(scope="module")
def source(tmp_path_factory):
    d = tmp_path_factory.mktemp("demo")
    db = built_lead_db(d / "source.duckdb")
    return db, write_snapshot(db, None, d / "snapshots" / "run")


def test_demo_reproduces_source_mart(source, tmp_path):
    db, snap = source
    run_demo(snap, tmp_path / "demo.duckdb", tmp_path / "out", 10)
    assert rows(tmp_path / "demo.duckdb", MART) == rows(db, MART)
    assert rows(tmp_path / "demo.duckdb",
                "select distinct sodra_source_url from marts.mart_leads "
                "where sodra_source_url is not null") == [("file:s.zip",)]


def test_demo_offline_and_repeatable(source, tmp_path, monkeypatch):
    _, snap = source

    def no_network(*a, **kw):
        raise AssertionError("network call in demo")

    monkeypatch.setattr(httpx.Client, "send", no_network)
    monkeypatch.setattr(httpx.AsyncClient, "send", no_network)
    for k in ("GOOGLE_API_KEY", "FIRECRAWL_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    opened = []
    real_connect = duckdb.connect
    monkeypatch.setattr(duckdb, "connect",
                        lambda *a, **kw: opened.append(str(a[0]) if a else "") or
                        real_connect(*a, **kw))
    db = tmp_path / "demo.duckdb"
    for out in ("a", "b"):  # twice into the same DB: replaced, not appended
        assert cli.main(["demo", "--snapshot", str(snap), "--out", str(tmp_path / out),
                         "--db", str(db)]) == 0
    assert opened and not any(p.endswith("leads.duckdb") for p in opened)
    assert rows(db, "select count(*) from raw.places") == [(7,)]
    for name in ("leads.csv", "leads.md"):
        assert (tmp_path / "a" / "2026-10" / name).read_bytes() == \
            (tmp_path / "b" / "2026-10" / name).read_bytes()


def test_demo_no_snapshot(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert cli.main(["demo"]) == 1
    err = capsys.readouterr().err
    assert err.count("\n") == 1 and "no snapshot" in err


def test_newest_snapshot_picks_latest_run_id(tmp_path):
    for name in ("2026-09_20260901T000000", "2026-10_20261019T173429", "2026-10_20261001T000000"):
        (tmp_path / name).mkdir()
        (tmp_path / name / "manifest.json").write_text("{}")
    (tmp_path / "zz_no_manifest").mkdir()
    assert newest_snapshot(tmp_path).name == "2026-10_20261019T173429"
