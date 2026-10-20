"""Runs the dbt project over the raw DuckDB file."""
import os
import subprocess
import sys
from pathlib import Path

DBT_DIR = Path(__file__).resolve().parent.parent / "dbt"


def run_dbt(args: list[str], db_path: Path) -> subprocess.CompletedProcess[str]:
    cmd = [
        str(Path(sys.executable).parent / "dbt"), *args,
        "--project-dir", str(DBT_DIR), "--profiles-dir", str(DBT_DIR),
    ]
    env = {**os.environ, "DB_PATH": str(Path(db_path).resolve())}
    return subprocess.run(cmd, env=env, capture_output=True, text=True)


def stdout_tail(result: subprocess.CompletedProcess[str], lines: int = 40) -> str:
    """The last `lines` lines of a dbt run's stdout, for a failure message."""
    return "\n".join((result.stdout or "").splitlines()[-lines:])
