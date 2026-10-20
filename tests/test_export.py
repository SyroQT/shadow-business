import csv
import re
from pathlib import Path

import duckdb
import pytest

from src.db import connect
from src.export import export_leads
from src.run import new_run
from tests.dbt_harness import build, load, load_lead_demo, page, place, rows, sodra


@pytest.fixture(scope="module")
def db(tmp_path_factory):
    path = tmp_path_factory.mktemp("export") / "t.duckdb"
    con = connect(path)
    load_lead_demo(con, new_run("2026-10"))
    con.close()
    build(path)
    return path


def md(out: Path) -> str:
    return (out / "2026-10" / "leads.md").read_text()


def cards(text: str) -> list[str]:
    return re.findall(r"```text\n(.*?)```", text, re.S)


def test_export_writes_both_files(db, tmp_path):
    assert export_leads(db, tmp_path, 0) == tmp_path / "2026-10"
    assert (tmp_path / "2026-10" / "leads.csv").is_file()
    assert (tmp_path / "2026-10" / "leads.md").is_file()


def test_csv_has_every_row(db, tmp_path):
    export_leads(db, tmp_path, 0)
    with (tmp_path / "2026-10" / "leads.csv").open(newline="", encoding="utf-8-sig") as f:
        data = list(csv.DictReader(f))
    con = duckdb.connect(str(db), read_only=True)
    cols = [r[0] for r in con.execute("describe marts.mart_leads").fetchall()]
    n = con.execute("select count(*) from marts.mart_leads").fetchone()[0]
    con.close()
    assert len(data) == n == 7
    assert list(data[0].keys()) == cols
    flag = next(r for r in data if r["tier"] == "flag")
    assert flag["places"] == "Cafe 5 (https://www.google.com/maps/place/?q=place_id:demo5)" or \
        re.fullmatch(r"Cafe 5 \(.+\)", flag["places"])


def test_top_zero_flag_and_watch_only(db, tmp_path):
    export_leads(db, tmp_path, 0)
    text = md(tmp_path)
    assert len(re.findall(r"^FLAG #1 ", text, re.M)) == 1
    assert len(re.findall(r"^WATCH #1 ", text, re.M)) == 1
    assert "CLEAR" not in text.split("```text", 1)[1]
    assert "Closest to the threshold" not in text


def test_top_n_adds_clear_cards(db, tmp_path):
    export_leads(db, tmp_path, 4)
    text = md(tmp_path)
    assert "Closest to the threshold (not flagged)" in text
    assert text.index("FLAG #1") < text.index("WATCH #1") < text.index("Closest to")
    clear = [c for c in cards(text) if c.startswith("CLEAR")]
    assert [c.split()[1] for c in clear] == ["#1", "#2"]
    assert "Cafe 1 " in clear[0] and "Cafe 2 " in clear[1]
    assert not re.search(r"^EXCLUDED", text, re.M)


def test_csv_same_with_and_without_top(db, tmp_path):
    export_leads(db, tmp_path / "a", 0)
    export_leads(db, tmp_path / "b", 10)
    assert (tmp_path / "a/2026-10/leads.csv").read_bytes() == \
        (tmp_path / "b/2026-10/leads.csv").read_bytes()


def test_summary_table(db, tmp_path):
    export_leads(db, tmp_path, 0)
    text = md(tmp_path)
    head = text.split("```text")[0]
    assert "| category" in head and "excluded" in head
    assert "cafés and restaurants" in head


def test_card_lines(db, tmp_path):
    export_leads(db, tmp_path, 0)
    flag = next(c for c in cards(md(tmp_path)) if c.startswith("FLAG"))
    for token in ("Busy:", "Paid:", "Match:", "Places:", "Note:", "Reason:",
                  "Last month: not present", "€1000.00", "×"):
        assert token in flag
    assert '"Įmonės kodas: 100000005"' in flag
    assert flag.count("Places:") == 1
    assert "Robust:" not in flag and "Checked:" not in flag
    assert flag.splitlines()[1].lstrip().startswith("Reason:")


def test_export_idempotent(db, tmp_path):
    export_leads(db, tmp_path, 4)
    first = [(p.name, p.read_bytes()) for p in sorted((tmp_path / "2026-10").iterdir())]
    export_leads(db, tmp_path, 4)
    assert first == [(p.name, p.read_bytes()) for p in sorted((tmp_path / "2026-10").iterdir())]


def test_export_latest_month_only(tmp_path):
    path = tmp_path / "t.duckdb"
    con = connect(path)
    load_lead_demo(con, new_run("2026-10"))
    load_lead_demo(con, new_run("2026-11"))
    con.close()
    build(path)
    out = tmp_path / "out"
    export_leads(path, out, 0)
    assert [p.name for p in out.iterdir()] == ["2026-11"]
    flag = next(c for c in cards((out / "2026-11" / "leads.md").read_text())
                if c.startswith("FLAG"))
    assert "Last month: flag" in flag


def test_export_no_rows(tmp_path):
    path = tmp_path / "t.duckdb"
    connect(path).close()
    build(path)
    out = tmp_path / "out"
    assert export_leads(path, out, 5) is None
    assert not out.exists()


def test_export_only_excluded(tmp_path):
    path = tmp_path / "t.duckdb"
    con = connect(path)
    run = new_run("2026-10")
    load(con, run,
         places=[place("p1", "cafes_restaurants", "Cafe X", website="https://x.lt/",
                       user_rating_count=5, rating=4.0)],
         pages=[page("p1", "home", "Įmonės kodas: 100000001", url="https://x.lt/")],
         sodra=[sodra("100000001", num_insured="10", avg_wage="1500.00")],
         steps={"places": "ok", "sodra": "ok"})
    con.close()
    build(path)
    out = tmp_path / "out"
    export_leads(path, out, 10)
    text = (out / "2026-10" / "leads.md").read_text()
    assert "| cafés and restaurants | 0 | 0 | 0 | 1 |" in text
    assert cards(text) == []


def test_card_places_and_minimum_wage_format(db, tmp_path):
    export_leads(db, tmp_path, 0)
    flag = next(c for c in cards(md(tmp_path)) if c.startswith("FLAG"))
    assert re.search(r"^  Places: Cafe 5 — \S+$", flag, re.M)
    assert "(€1153)" in flag


def test_card_wage_multiple_two_decimals_and_sodra_month(db, tmp_path):
    export_leads(db, tmp_path, 0)
    flag = next(c for c in cards(md(tmp_path)) if c.startswith("FLAG"))
    assert re.search(r"Paid:.*, \d\.\d\d× the minimum wage", flag)
    assert re.search(r"data month \d{4}-\d{2}$", flag, re.M)


def test_csv_order_leads_first_excluded_last(db, tmp_path):
    export_leads(db, tmp_path, 0)
    with (tmp_path / "2026-10" / "leads.csv").open(newline="", encoding="utf-8-sig") as f:
        data = list(csv.DictReader(f))
    assert data[0]["tier"] == "flag" and data[1]["tier"] == "watch"
    tiers = [r["tier"] for r in data]
    assert tiers.index("excluded") > max(i for i, t in enumerate(tiers) if t != "excluded")
    assert (tmp_path / "2026-10" / "leads.csv").read_bytes().startswith(b"\xef\xbb\xbf")


def test_md_heading_and_thresholds(db, tmp_path):
    export_leads(db, tmp_path, 0)
    lines = md(tmp_path).splitlines()
    assert lines[0] == "# Leads — run 2026-10"
    assert any(line.startswith("Thresholds: busy ≥ 2.0×") and "€1153" in line for line in lines)
    assert "No flag or watch leads" not in "\n".join(lines)


def test_md_no_leads_line(tmp_path):
    path = tmp_path / "t.duckdb"
    con = connect(path)
    run = new_run("2026-10")
    spec = [(300, "1500.00")] * 5
    load(con, run,
         places=[place(f"p{i}", "cafes_restaurants", f"Cafe {i}", website=f"https://c{i}.lt/",
                       user_rating_count=r, rating=4.0) for i, (r, _) in enumerate(spec)],
         pages=[page(f"p{i}", "home", f"Įmonės kodas: 10000000{i}", url=f"https://c{i}.lt/")
                for i in range(5)],
         sodra=[sodra(f"10000000{i}", num_insured="10", avg_wage=w)
                for i, (_, w) in enumerate(spec)],
         steps={"places": "ok", "sodra": "ok"})
    con.close()
    build(path)
    export_leads(path, tmp_path / "out", 0)
    text = (tmp_path / "out" / "2026-10" / "leads.md").read_text()
    assert "No flag or watch leads this month." in text
    assert cards(text) == []


def test_export_skips_latest_month_without_matches(tmp_path):
    path = tmp_path / "t.duckdb"
    con = connect(path)
    load_lead_demo(con, new_run("2026-10"))
    load(con, new_run("2026-11"),
         places=[place("n1", "cafes_restaurants", "Nocode", website="https://n.lt/",
                       user_rating_count=500, rating=4.0)],
         pages=[page("n1", "home", "Nieko čia nėra", url="https://n.lt/")],
         steps={"places": "ok", "sodra": "ok"})
    con.close()
    build(path)
    out = tmp_path / "out"
    assert export_leads(path, out, 0) is None
    assert not out.exists()


def test_main_missing_db_returns_one(tmp_path, monkeypatch, capsys):
    from types import SimpleNamespace

    from src import export
    monkeypatch.setattr(export, "load_settings_or_exit",
                        lambda: SimpleNamespace(db_path=tmp_path / "nope.duckdb", leads_top_n=0))
    assert export.main(["--out", str(tmp_path / "o")]) == 1
    err = capsys.readouterr().err
    assert "cannot export" in err and "Traceback" not in err


def test_main_db_without_mart_returns_one(tmp_path, monkeypatch, capsys):
    from types import SimpleNamespace

    from src import export
    path = tmp_path / "empty.duckdb"
    connect(path).close()
    monkeypatch.setattr(export, "load_settings_or_exit",
                        lambda: SimpleNamespace(db_path=path, leads_top_n=0))
    assert export.main(["--out", str(tmp_path / "o")]) == 1
    assert "cannot export" in capsys.readouterr().err


def _main_calls(monkeypatch, result):
    from types import SimpleNamespace

    from src import export
    calls = []
    monkeypatch.setattr(export, "load_settings_or_exit",
                        lambda: SimpleNamespace(db_path=Path("x.duckdb"), leads_top_n=7))
    monkeypatch.setattr(export, "export_leads", lambda *a: calls.append(a) or result)
    return export, calls


def test_main_defaults_from_settings(monkeypatch):
    export, calls = _main_calls(monkeypatch, Path("output/2026-10"))
    assert export.main([]) == 0
    assert calls == [(Path("x.duckdb"), Path("output"), 7)]


def test_main_overrides_and_empty_returns_zero(monkeypatch):
    export, calls = _main_calls(monkeypatch, None)
    assert export.main(["--top", "3", "--out", "o"]) == 0
    assert calls == [(Path("x.duckdb"), Path("o"), 3)]
