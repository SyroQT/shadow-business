import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from src import pipeline


def done(code, out=""):
    return subprocess.CompletedProcess(["dbt"], code, stdout=out, stderr="")


@pytest.fixture
def calls(monkeypatch):
    calls = {"dbt": []}
    monkeypatch.setattr(pipeline, "load_settings_or_exit",
                        lambda: SimpleNamespace(db_path=Path("x.duckdb"), leads_top_n=10))
    monkeypatch.setattr(pipeline, "run_dbt",
                        lambda args, db: calls["dbt"].append((args, db)) or calls["result"])
    calls["result"] = done(0)
    calls["export"] = []
    monkeypatch.setattr(pipeline, "export_leads",
                        lambda *a: calls["export"].append(a) or Path("output/2026-10"))
    return calls


def test_dbt_runs_after_successful_extract(monkeypatch, calls):
    monkeypatch.setattr(pipeline, "extract_main", lambda argv: 0)
    assert pipeline.main() == 0
    assert calls["dbt"] == [(["build"], Path("x.duckdb"))]


def test_dbt_skipped_when_extract_fails(monkeypatch, calls):
    monkeypatch.setattr(pipeline, "extract_main", lambda argv: 1)
    assert pipeline.main() == 1
    assert calls["dbt"] == []


def test_dbt_failure_fails_pipeline(monkeypatch, calls, capsys):
    monkeypatch.setattr(pipeline, "extract_main", lambda argv: 0)
    calls["result"] = done(1, "\n".join(f"line{i}" for i in range(100)))
    assert pipeline.main() == 1
    err = capsys.readouterr().err
    assert "line99" in err and "line10\n" not in err


def test_export_runs_after_dbt(monkeypatch, calls):
    monkeypatch.setattr(pipeline, "extract_main", lambda argv: 0)
    assert pipeline.main() == 0
    assert calls["export"] == [(Path("x.duckdb"), Path("output"), 10)]


def test_export_skipped_when_dbt_fails(monkeypatch, calls):
    monkeypatch.setattr(pipeline, "extract_main", lambda argv: 0)
    calls["result"] = done(1)
    assert pipeline.main() == 1
    assert calls["export"] == []


def test_export_error_fails_pipeline(monkeypatch, calls):
    monkeypatch.setattr(pipeline, "extract_main", lambda argv: 0)

    def boom(*a):
        raise RuntimeError("disk full")

    monkeypatch.setattr(pipeline, "export_leads", boom)
    assert pipeline.main() == 1
