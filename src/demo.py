"""Rebuild the leads from a snapshot: parquet -> fresh DuckDB -> dbt build -> export. Offline."""
from pathlib import Path

from src.db import connect
from src.export import export_leads
from src.snapshot import SNAPSHOT_ROOT, TABLES
from src.transform import run_dbt

STDOUT_TAIL_LINES = 20


class DemoError(Exception):
    pass


def newest_snapshot(root: Path = SNAPSHOT_ROOT) -> Path:
    found = sorted(p.parent for p in Path(root).glob("*/manifest.json"))
    if not found:
        raise DemoError(f"no snapshot under {root}/ (expected <run_id>/manifest.json)")
    return found[-1]


def run_demo(snapshot_dir: Path, db_path: Path, out_dir: Path, top_n: int) -> Path | None:
    snapshot_dir, db_path = Path(snapshot_dir), Path(db_path)
    if not (snapshot_dir / "manifest.json").exists():
        raise DemoError(f"{snapshot_dir} has no manifest.json")
    for p in (db_path, db_path.with_name(db_path.name + ".wal")):
        p.unlink(missing_ok=True)
    con = connect(db_path)
    try:
        for t in TABLES:
            f = snapshot_dir / f"{t}.parquet"
            if not f.exists():
                raise DemoError(f"{f} missing")
            con.execute(f"insert into raw.{t} by name select * from read_parquet(?)", [str(f)])
    finally:
        con.close()
    res = run_dbt(["build"], db_path)
    if res.returncode != 0:
        tail = (res.stdout or "").splitlines()[-STDOUT_TAIL_LINES:]
        raise DemoError("dbt build failed:\n" + "\n".join(tail))
    return export_leads(db_path, Path(out_dir), top_n)
