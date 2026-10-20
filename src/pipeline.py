"""Entrypoint: extract -> dbt build -> export."""
import sys
import time
from pathlib import Path

from src.export import export_leads
from src.extract.__main__ import main as extract_main
from src.log import log
from src.settings import load_settings_or_exit
from src.transform import run_dbt

STDOUT_TAIL_LINES = 40


def main() -> int:
    code = extract_main(["all"])
    if code != 0:
        log("step", step="dbt", status="skipped", reason="extract_failed")
        return code
    settings = load_settings_or_exit()
    started = time.monotonic()
    result = run_dbt(["build"], settings.db_path)
    duration = round(time.monotonic() - started, 1)
    ok = result.returncode == 0
    log("step", step="dbt", status="ok" if ok else "failed", duration_s=duration)
    if not ok:
        tail = (result.stdout or "").splitlines()[-STDOUT_TAIL_LINES:]
        print("\n".join(tail), file=sys.stderr)
        return 1
    try:
        path = export_leads(settings.db_path, Path("output"), settings.leads_top_n)
    except Exception as exc:
        log("step", step="export", status="failed", error=f"{type(exc).__name__}: {exc}")
        return 1
    log("step", step="export", status="ok" if path else "no_rows",
        path=str(path) if path else None)
    return 0


if __name__ == "__main__":
    sys.exit(main())
