import json
import zipfile
from pathlib import Path

import pytest

from src.cache import sodra_client
from src.extract import sodra
from src.extract.sodra import EXPECTED_COLUMNS, map_headers, run_sodra, sniff, validate
from src.run import new_run
from src.settings import Settings

FIXTURE = Path(__file__).parent / "fixtures" / "sodra" / "monthly-sample.csv"

REAL_HEADER = (
    "Draudėjo kodas (code);Juridinių asmenų registro kodas (jarCode);Pavadinimas (name);"
    "Savivaldybė, kurioje registruota(municipality);Ekonominės veiklos rūšies kodas(ecoActCode);"
    "Ekonominės veiklos rūšies kodas EVRK(ecoActCodeStr);Ekonominės veiklos rūšies pavadinimas(ecoActName);"
    "Mėnuo (month);Vidutinis darbo užmokestis (avgWage);Apdraustųjų skaičius (numInsured);"
    "Vidutinis darbo užmokestis II (avgWage2);Apdraustųjų skaičius II (numInsured2);"
    "Valstybinio socialinio draudimo įmoka (tax)"
)
RUN = new_run("2026-10")


def make_zip(tmp_path, text: str | None = None, name="in.zip") -> Path:
    p = tmp_path / name
    with zipfile.ZipFile(p, "w") as z:
        if text is None:
            z.write(FIXTURE, "monthly-2026.csv")
        else:
            z.writestr("monthly-2026.csv", text.encode("utf-8-sig"))
    return p


def fixture_text() -> str:
    return FIXTURE.read_bytes().decode("utf-8-sig")


def go(con, tmp_path, zip_path, **kw):
    s = Settings(google_api_key="g", firecrawl_api_key="f", _env_file=None, sodra_file=zip_path)
    return run_sodra(con, s, sodra_client(s), RUN, min_rows_per_month=10,
                     dest_dir=tmp_path / "dest", **kw)


def count(con):
    return con.execute("select count(*) from raw.sodra").fetchone()[0]


def test_map_headers_real_header():
    assert map_headers(REAL_HEADER.split(";")) == EXPECTED_COLUMNS


def test_map_headers_missing_token():
    with pytest.raises(ValueError, match="Pavadinimas"):
        map_headers(["Pavadinimas", "x (code)"])


def test_sniff_bom_semicolon():
    assert sniff(FIXTURE.read_bytes()[:4096]) == ("utf-8-sig", ";")
    assert sniff("a,b\n1,2\n".encode()) == ("utf-8", ",")
    assert sniff("a;b\nį;2\n".encode("cp1257")) == ("cp1257", ";")


def test_load_fixture(con, tmp_path):
    stats = go(con, tmp_path, make_zip(tmp_path))
    assert stats.items_ok == 20
    assert count(con) == 20
    codes = {r[0] for r in con.execute('select code from raw.sodra').fetchall()}
    assert "0012345" in codes
    jar = con.execute("select \"jarCode\" from raw.sodra where code='42399' limit 1").fetchone()[0]
    assert jar in (None, "")
    assert con.execute('select count(*) from raw.sodra where data_month != "month"').fetchone()[0] == 0
    assert con.execute("select count(distinct retrieved_at), count(distinct run_id) from raw.sodra").fetchone() == (1, 1)
    assert con.execute("select status, items_ok from raw.run_log").fetchall() == [("ok", 20)]
    names = [r[0] for r in con.execute("select column_name from information_schema.columns where table_name='sodra'").fetchall()]
    assert "code" in names


def test_validation_fails_on_gap_month(con, tmp_path):
    text = fixture_text().replace("202602", "202603")
    with pytest.raises(ValueError, match="contiguous"):
        go(con, tmp_path, make_zip(tmp_path, text))
    assert count(con) == 0
    assert con.execute("select status from raw.run_log").fetchall() == [("failed",)]


def test_validation_fails_on_bad_jar(con, tmp_path):
    text = fixture_text().replace("30000000", "3000000").replace("30000001", "3000001")
    with pytest.raises(ValueError, match="jarCode"):
        go(con, tmp_path, make_zip(tmp_path, text))
    assert count(con) == 0


def test_validation_fails_on_bad_num_insured(con, tmp_path):
    text = fixture_text().replace(";4;;0;2510.18", ";x;;0;2510.18", 1)
    with pytest.raises(ValueError, match="numInsured"):
        go(con, tmp_path, make_zip(tmp_path, text))


def test_validation_fails_on_missing_column(con, tmp_path):
    text = fixture_text().replace("(tax)", "(taxx)")
    with pytest.raises(ValueError, match="tax"):
        go(con, tmp_path, make_zip(tmp_path, text))
    assert count(con) == 0


def test_validation_fails_on_not_starting_at_01(con, tmp_path):
    text = fixture_text().replace("202602", "202603").replace("202601", "202602")
    with pytest.raises(ValueError, match="start"):
        go(con, tmp_path, make_zip(tmp_path, text))


def test_validation_fails_on_min_rows(con, tmp_path):
    s = Settings(google_api_key="g", firecrawl_api_key="f", _env_file=None, sodra_file=make_zip(tmp_path))
    with pytest.raises(ValueError, match="rows"):
        run_sodra(con, s, sodra_client(s), RUN, min_rows_per_month=11, dest_dir=tmp_path / "dest")
    assert count(con) == 0


def test_duplicate_sha_skips_insert(con, tmp_path):
    z = make_zip(tmp_path)
    go(con, tmp_path, z)
    stats = go(con, tmp_path, z)
    assert count(con) == 20
    assert stats.items_ok == 0
    rows = con.execute("select status, notes from raw.run_log order by started_at").fetchall()
    assert [r[0] for r in rows] == ["ok", "ok"]
    assert "duplicate_sha=" in rows[1][1]


def test_report_prints(con, tmp_path, capsys):
    go(con, tmp_path, make_zip(tmp_path))
    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    ev = [r for r in lines if r["event"] == "sodra_validation"]
    assert len(ev) == 1
    assert ev[0]["rows"] == 20 and ev[0]["months"] == {"202601": 10, "202602": 10}
    assert ev[0]["failures"] == [] and ev[0]["bad_num_insured"] == 0
    assert ev[0]["jar9_share"] == 1.0
    assert not any("Sodra validation report" in line for line in capsys.readouterr().out.splitlines())


def test_temp_dir_removed(con, tmp_path, monkeypatch):
    import tempfile
    seen = []
    orig = tempfile.TemporaryDirectory

    class Spy(orig):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            seen.append(self.name)

    monkeypatch.setattr(sodra.tempfile, "TemporaryDirectory", Spy)
    go(con, tmp_path, make_zip(tmp_path))
    assert seen and not Path(seen[0]).exists()
