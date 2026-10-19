from datetime import datetime, timedelta

from src.db import insert_rows
from src.extract.__main__ import main
from src.extract.report import build_report, latest_run_id, write_report
from src.run import RunContext

T0 = datetime(2026, 10, 19, 10, 0, 0)
RUN = RunContext("2026-10_20261019T100000", "2026-10", T0)


def log_row(step, start_min, end_min, notes=None, api=0, hits=0, status="ok"):
    return {"run_month": "2026-10", "step": step, "started_at": T0 + timedelta(minutes=start_min),
            "finished_at": T0 + timedelta(minutes=end_min), "status": status, "items_ok": 0,
            "items_failed": 0, "api_requests": api, "cache_hits": hits, "notes": notes}


def place(pid, category, web):
    payload = {"id": pid, "websiteUri": web} if web else {"id": pid}
    return {"category": category, "tile_id": "t", "request_hash": "h", "place_id": pid,
            "payload": payload}


def page(pid, skip, role="home"):
    return {"place_id": pid, "page_role": role, "requested_url": f"https://{pid}.lt",
            "final_url": None, "http_status": 200, "skip_reason": skip, "markdown": None,
            "links": None, "content_sha256": None}


def sodra_rows(n_jar, n_empty, month="202610"):
    rows = [{"source_url": "u", "file_sha256": "abc", "data_month": month, "code": str(i),
             "jarCode": f"J{i}"} for i in range(n_jar)]
    rows += [{"source_url": "u", "file_sha256": "abc", "data_month": month, "code": f"e{i}",
              "jarCode": ""} for i in range(n_empty)]
    return rows


def fill(con):
    insert_rows(con, "run_log", [
        log_row("sodra", 0, 2, api=1),
        log_row("places", 2, 12, notes="cafes_restaurants:saturated_tiles=2; "
                "cafes_restaurants:coverage_gaps=1", api=7, hits=3),
        log_row("crawl", 12, 30, notes="firecrawl_errors=1", api=5, hits=1, status="partial"),
    ], RUN)
    insert_rows(con, "places", [place("p1", "cafes_restaurants", "https://a.lt"),
                                place("p2", "cafes_restaurants", "https://b.lt"),
                                place("p3", "cafes_restaurants", None)], RUN)
    insert_rows(con, "web_pages", [page("p1", None), page("p2", "error"),
                                   page("p3", "no_website"),
                                   page("p1", None, role="legal_candidate")], RUN)
    insert_rows(con, "sodra", sodra_rows(18, 2), RUN)


def test_report_from_db_only(con):
    fill(con)
    md = build_report(con, RUN.run_id)
    assert "| cafes_restaurants | 3 | 3 |" in md
    assert "no_website | 1" in md
    assert "Sodra rows: 20" in md
    assert "jarCode share: 90.0%" in md
    assert "Website share: 66.7%" in md
    assert "Crawled share: 33.3%" in md
    assert "| cafes_restaurants | 2 | 1 |" in md  # saturated tiles, coverage gaps
    assert "| places | ok | 7 | 3 | 10m 0s |" in md
    assert "Total duration: 30m 0s" in md


def test_empty_sections(con):
    insert_rows(con, "run_log", [log_row("places", 0, 1)], RUN)
    md = build_report(con, RUN.run_id)
    assert "Sodra: no data landed" in md
    assert "Website share: n/a" in md and "Crawled share: n/a" in md


def test_sodra_duplicate_sha_run_uses_earlier_landing(con):
    old = RunContext("2026-09_20260919T100000", "2026-09", T0 - timedelta(days=30))
    row = log_row("sodra", 0, 1)
    row.update(run_month="2026-09", started_at=T0 - timedelta(days=30),
               finished_at=T0 - timedelta(days=30))
    insert_rows(con, "run_log", [row], old)
    insert_rows(con, "sodra", sodra_rows(9, 1), old)
    insert_rows(con, "run_log", [log_row("sodra", 0, 1, notes="duplicate_sha=abc"),
                                 log_row("places", 1, 2)], RUN)
    md = build_report(con, RUN.run_id)
    assert "Sodra rows: 10" in md and "jarCode share: 90.0%" in md


def test_sodra_ignores_later_landings(con):
    later = RunContext("2026-11_20261119T100000", "2026-11", T0 + timedelta(days=30))
    row = log_row("sodra", 0, 1)
    row.update(run_month="2026-11", started_at=T0 + timedelta(days=30),
               finished_at=T0 + timedelta(days=30))
    insert_rows(con, "run_log", [row], later)
    insert_rows(con, "sodra", sodra_rows(5, 0), later)
    insert_rows(con, "run_log", [log_row("places", 1, 2)], RUN)
    assert "Sodra: no data landed" in build_report(con, RUN.run_id)


def test_write_report_path(con, tmp_path):
    fill(con)
    p = write_report(con, RUN.run_id, out_root=tmp_path / "out")
    assert p == tmp_path / "out" / "2026-10" / "extract_report.md"
    assert p.read_text() == build_report(con, RUN.run_id)


def test_latest_run_id(con):
    assert latest_run_id(con, "2026-10") is None
    fill(con)
    assert latest_run_id(con, "2026-10") == RUN.run_id
    assert latest_run_id(con, "2026-11") is None


def test_cli_report(con, tmp_path, monkeypatch, capsys):
    from src.extract import __main__ as m
    from src.settings import Settings
    fill(con)
    con.close()
    monkeypatch.chdir(tmp_path)
    s = Settings(google_api_key="g", firecrawl_api_key="f", _env_file=None,
                 db_path=tmp_path / "sub" / "t.duckdb", run_month="2026-10")
    monkeypatch.setattr(m, "load_settings", lambda: s)
    assert main(["report"]) == 0
    assert (tmp_path / "output" / "2026-10" / "extract_report.md").exists()
    assert "extract_report.md" in capsys.readouterr().out
    assert main(["report", "--run-id", RUN.run_id]) == 0


def test_cli_report_no_run(tmp_path, monkeypatch, capsys):
    from src.extract import __main__ as m
    from src.settings import Settings
    s = Settings(google_api_key="g", firecrawl_api_key="f", _env_file=None,
                 db_path=tmp_path / "x.duckdb", run_month="2026-10")
    monkeypatch.setattr(m, "load_settings", lambda: s)
    assert main(["report"]) == 2
    assert "no run" in capsys.readouterr().err
