"""Entrypoint: extract -> dbt build -> export."""
import sys
import time

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
    log("not_implemented", step="export", message="export is not implemented yet")
    return 0


if __name__ == "__main__":
    sys.exit(main())
