"""Live run body for `python -m src.cli full`: extract -> dbt build -> export."""
import sys
import time
from pathlib import Path

from src.export import export_leads
from src.extract.__main__ import extract_run
from src.log import log
from src.settings import Settings
from src.transform import run_dbt, stdout_tail


def run(settings: Settings) -> tuple[int, str]:
    """Returns (exit code, run_id of the extraction)."""
    code, run_id = extract_run(settings)
    if code != 0:
        log("step", step="dbt", status="skipped", reason="extract_failed")
        return code, run_id
    started = time.monotonic()
    result = run_dbt(["build"], settings.db_path)
    duration = round(time.monotonic() - started, 1)
    ok = result.returncode == 0
    log("step", step="dbt", status="ok" if ok else "failed", duration_s=duration)
    if not ok:
        print(stdout_tail(result), file=sys.stderr)
        return 1, run_id
    try:
        path = export_leads(settings.db_path, Path("output"), settings.leads_top_n)
    except Exception as exc:
        log("step", step="export", status="failed", error=f"{type(exc).__name__}: {exc}")
        return 1, run_id
    log("step", step="export", status="ok" if path else "no_rows",
        path=str(path) if path else None)
    return 0, run_id
