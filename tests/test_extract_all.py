from pathlib import Path

import httpx
import pytest

from src.cache import SourceClient, TokenBucket
from src.extract.__main__ import extract_all, main
from src.run import new_run
from src.settings import Settings
from src.extract.crawl import targets
from tests.replay import load_recordings, replay_transport
from tests.test_places import BBOX, config, make_client, partial_page
from tests.test_sodra_load import make_zip

RUN = new_run("2026-10")


def mk(handler, name="x"):
    return SourceClient(name, None, TokenBucket(1000, sleep=lambda s: None), None, {},
                        transport=httpx.MockTransport(handler), sleep=lambda s: None,
                        follow_redirects=True)


def fc_handler(calls):
    def h(request):
        calls.append(str(request.url))
        return httpx.Response(200, json={"success": False, "error": "nope"})
    return h


def robots_handler(request):
    return httpx.Response(404, text="")


def setup(tmp_path, monkeypatch, places_handler=partial_page, places_max=None, sodra_ok=True):
    monkeypatch.chdir(tmp_path)  # run_sodra lands the zip under ./data/raw/sodra
    settings = Settings(google_api_key="g", firecrawl_api_key="f", _env_file=None,
                        sodra_file=make_zip(tmp_path), max_firecrawl_pages=100)
    places, _ = make_client(tmp_path / "pc", places_handler, max_requests=places_max)
    fc_calls = []
    clients = {"sodra": mk(robots_handler), "places": places,
               "firecrawl": mk(fc_handler(fc_calls)), "robots": mk(robots_handler)}
    return settings, config({"a": ["cafe"]}), clients, fc_calls


def rows(con, run_id=RUN.run_id):
    return dict(con.execute("select step, status from raw.run_log where run_id = ?",
                            [run_id]).fetchall())


def test_all_writes_three_run_log_rows_same_run_id(con, tmp_path, monkeypatch):
    settings, cfg, clients, _ = setup(tmp_path, monkeypatch)
    st = extract_all(settings, cfg, con, RUN, clients, sodra_min_rows=10)
    assert set(rows(con)) == {"sodra", "places", "crawl"}
    assert con.execute("select count(*) from raw.run_log").fetchone()[0] == 3
    assert st == rows(con)
    assert st["sodra"] == "ok" and st["places"] == "ok"


def test_exit_nonzero_on_failed_step(con, tmp_path, monkeypatch):
    settings, cfg, clients, _ = setup(tmp_path, monkeypatch)
    st = extract_all(settings, cfg, con, RUN, clients)  # 20 rows < 50_000 -> validation fails
    assert st["sodra"] == "failed"
    assert st["places"] == "ok" and st["crawl"] in ("ok", "partial")
    assert con.execute("select count(*) from raw.places").fetchone()[0] > 0


def test_capped_is_exit_zero(con, tmp_path, monkeypatch):
    from src.extract import __main__ as m
    settings, cfg, clients, _ = setup(tmp_path, monkeypatch, places_max=0)
    st = extract_all(settings, cfg, con, RUN, clients, sodra_min_rows=10)
    assert st["places"] == "capped"
    assert m._exit_code(st) == 0


def test_exit_code_helper():
    from src.extract.__main__ import _exit_code
    assert _exit_code({"a": "ok", "b": "partial", "c": "capped"}) == 0
    assert _exit_code({"a": "ok", "b": "failed"}) == 1


def test_crawl_skipped_when_places_failed(con, tmp_path, monkeypatch):
    settings, cfg, clients, fc_calls = setup(
        tmp_path, monkeypatch, places_handler=lambda b: httpx.Response(403, json={"error": {}}))
    st = extract_all(settings, cfg, con, RUN, clients, sodra_min_rows=10)
    assert st == {"sodra": "ok", "places": "failed", "crawl": "failed"}
    note = con.execute("select notes from raw.run_log where step='crawl'").fetchone()[0]
    assert "upstream_places_failed" in note
    assert fc_calls == [] and clients["firecrawl"].api_requests == 0
    assert clients["robots"].api_requests == 0


def test_raising_step_does_not_stop_next(con, tmp_path, monkeypatch, capsys):
    settings, cfg, clients, _ = setup(tmp_path, monkeypatch)
    del clients["sodra"]
    settings.sodra_file = None  # no source at all -> sodra raises
    st = extract_all(settings, cfg, con, RUN, clients, sodra_min_rows=10)
    assert st["sodra"] == "failed" and st["places"] == "ok"
    out = capsys.readouterr().out
    assert '"event": "error"' in out and '"step": "sodra"' in out


def test_crawl_without_places_run_exits_2(tmp_path, monkeypatch, capsys):
    from src.extract import __main__ as m
    from src.db import connect
    settings = Settings(google_api_key="g", firecrawl_api_key="f", _env_file=None,
                        db_path=tmp_path / "x.duckdb", run_month="2026-10")
    monkeypatch.setattr(m, "load_settings_or_exit", lambda: settings)
    monkeypatch.setattr(m, "load_config", lambda p: config({"a": ["cafe"]}))
    assert main(["crawl"]) == 2
    assert "no places run" in capsys.readouterr().err


def test_latest_places_run(con):
    from src.extract.__main__ import _latest_places_run
    from src.db import insert_rows
    assert _latest_places_run(con, "2026-10") is None
    for rid, t in (("2026-10_A", "2026-10-01 00:00:00"), ("2026-10_B", "2026-10-02 00:00:00")):
        r = new_run("2026-10")
        insert_rows(con, "run_log", [{"run_month": "2026-10", "step": "places", "started_at": t,
                                      "finished_at": t, "status": "ok", "items_ok": 0,
                                      "items_failed": 0, "api_requests": 0, "cache_hits": 0,
                                      "notes": None}], type(r)(rid, "2026-10", r.started_at))
    assert _latest_places_run(con, "2026-10") == "2026-10_B"
    assert _latest_places_run(con, "2026-11") is None


@pytest.mark.recorded
def test_e2e_recorded(con, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cfg = config({"cafes_restaurants": ["cafe", "restaurant"]}, tile_km=1.0, min_tile_km=1.0)
    cfg.crawl.legal_link_pattern = "(?!x)x"  # never matches; legal pages were not recorded
    settings = Settings(google_api_key="g", firecrawl_api_key="f", _env_file=None,
                        sodra_file=make_zip(tmp_path), max_firecrawl_pages=1000)

    def fc_fallback(request):
        return httpx.Response(200, json={"success": False, "error": "not recorded"})

    def robots_fallback(request):
        return httpx.Response(404, text="")

    def rc(source, transport, **kw):
        return SourceClient(source, None, TokenBucket(1000, sleep=lambda s: None), None, {},
                            transport=transport, sleep=lambda s: None, **kw)

    clients = {
        "sodra": mk(robots_handler),
        "places": rc("places", replay_transport("places")),
        "firecrawl": rc("firecrawl", replay_transport("firecrawl", fallback=fc_fallback)),
        "robots": rc("robots", replay_transport("robots", fallback=robots_fallback),
                     follow_redirects=True),
    }
    st = extract_all(settings, cfg, con, RUN, clients, sodra_min_rows=10)
    log = rows(con)
    assert len(log) == 3 and log == st
    assert st["sodra"] == "ok" and st["crawl"] == "partial"
    assert st["places"] in ("ok", "partial", "capped")
    assert con.execute("select count(*) from raw.sodra").fetchone()[0] == 20
    # Only the cafe/restaurant tile was replayed, so just the recorded homepages whose
    # site is among those places' targets can land with markdown.
    recorded_urls = {r["request"]["body"]["url"] for r in load_recordings("firecrawl").values()}
    wanted = {t.url for t in targets(con, RUN.run_id, cfg.crawl) if t.cls == "crawl"} & recorded_urls
    landed = {u for (u,) in con.execute(
        "select requested_url from raw.web_pages where page_role='home' and markdown is not null"
    ).fetchall()}
    assert landed == wanted and len(landed) > 0
    bad = con.execute("select count(*) from raw.web_pages where skip_reason = 'error'").fetchone()[0]
    assert bad > 0
    assert con.execute(
        "select count(*) from raw.web_pages where page_role='home' and markdown is null"
        " and skip_reason is null").fetchone()[0] == 0


def test_all_missing_keys_record_failed_steps(con, tmp_path, monkeypatch):
    settings, cfg, clients, _ = setup(tmp_path, monkeypatch)
    settings = Settings(_env_file=None, sodra_file=settings.sodra_file, cache_dir=tmp_path / "c",
                        config_path=Path(__file__).resolve().parent.parent / "config/categories.yaml")
    keyless = {"sodra": clients["sodra"], "robots": clients["robots"]}
    st = extract_all(settings, cfg, con, RUN, keyless, sodra_min_rows=10)
    assert st == {"sodra": "ok", "places": "failed", "crawl": "failed"}
    notes = dict(con.execute("select step, notes from raw.run_log").fetchall())
    assert notes["places"] == "missing_setting=GOOGLE_API_KEY"
    assert "upstream_places_failed" in notes["crawl"]


def test_all_missing_firecrawl_key_fails_only_crawl(con, tmp_path, monkeypatch):
    settings, cfg, clients, _ = setup(tmp_path, monkeypatch)
    settings = Settings(_env_file=None, sodra_file=settings.sodra_file, cache_dir=tmp_path / "c",
                        config_path=Path(__file__).resolve().parent.parent / "config/categories.yaml")
    del clients["firecrawl"]
    st = extract_all(settings, cfg, con, RUN, clients, sodra_min_rows=10)
    assert st["places"] == "ok" and st["crawl"] == "failed"
    note = con.execute("select notes from raw.run_log where step='crawl'").fetchone()[0]
    assert note == "missing_setting=FIRECRAWL_API_KEY"


def test_places_main_missing_key_exits_2(con, tmp_path, monkeypatch, capsys):
    from src.extract import places
    s = Settings(_env_file=None, cache_dir=tmp_path)
    monkeypatch.setattr(places, "load_settings_or_exit", lambda: s)
    code = places.main([], con=con, config=config({"a": ["cafe"]}))
    assert code == 2 and "missing setting: GOOGLE_API_KEY" in capsys.readouterr().err


def test_sodra_main_uses_run_month(tmp_path, monkeypatch, capsys):
    from src.extract import sodra
    s = Settings(_env_file=None, db_path=tmp_path / "x.duckdb", run_month="2026-03",
                 cache_dir=tmp_path / "c", sodra_file=make_zip(tmp_path),
                 config_path=Path(__file__).resolve().parent.parent / "config/categories.yaml")
    monkeypatch.setattr(sodra, "load_settings_or_exit", lambda: s)
    monkeypatch.chdir(tmp_path)
    sodra.main()
    import duckdb
    con = duckdb.connect(str(s.db_path))
    assert con.execute("select run_id, run_month from raw.run_log").fetchone()[0].startswith("2026-03_")
