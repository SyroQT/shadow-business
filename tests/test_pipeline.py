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
                        lambda: SimpleNamespace(db_path=Path("x.duckdb")))
    monkeypatch.setattr(pipeline, "run_dbt",
                        lambda args, db: calls["dbt"].append((args, db)) or calls["result"])
    calls["result"] = done(0)
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
