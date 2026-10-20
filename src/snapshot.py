"""Save one run's raw rows as parquet + manifest.json, so `demo` can rebuild its leads offline."""
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import duckdb

SNAPSHOT_ROOT = Path("snapshots")
KEY_RE = r"AIza[0-9A-Za-z_-]{20,}|fc-[0-9a-f]{20,}"
REDACTED = "[redacted-key]"
TABLES = ("places", "places_errors", "web_pages", "sodra", "run_log")
NOTE = ("Raw rows of one run for offline replay (python -m src.cli demo). Local Sodra file "
        "paths are reduced to the file name; key-shaped strings in crawled pages are redacted.")


class SnapshotError(Exception):
    pass


def latest_complete_run(con: duckdb.DuckDBPyConnection) -> str | None:
    return con.execute(
        "select max(run_id) from (select run_id from src.raw.run_log "
        "where step in ('places', 'crawl') group by 1 having count(distinct step) = 2)"
    ).fetchone()[0]


def _q(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"


def _git_commit() -> str | None:
    try:
        res = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True)
    except OSError:
        return None
    if res.returncode != 0:
        return None
    return res.stdout.strip() or None


def _select(con: duckdb.DuckDBPyConnection, run_id: str, run_month: str) -> None:
    """One temp table per snapshot file."""
    con.execute("create temp table s_places as select * from src.raw.places where run_id = ?",
                [run_id])
    con.execute("create temp table s_places_errors as "
                "select * from src.raw.places_errors where run_id = ?", [run_id])
    # Crawled pages can embed the site's own Google Maps key; redact key-shaped strings so a
    # public snapshot never carries one (matching only reads standalone 9-digit codes).
    con.execute(f"""
        create temp table s_web_pages as
        select * replace (
            regexp_replace(markdown, {_q(KEY_RE)}, {_q(REDACTED)}, 'g') as markdown,
            regexp_replace(links::varchar, {_q(KEY_RE)}, {_q(REDACTED)}, 'g')::json as links)
        from src.raw.web_pages where run_id = ?""", [run_id])
    # dbt pairs a places month with that month's Sodra run, else the latest earlier one
    # (int_run_pairs), so every Sodra run up to the run month goes in.
    con.execute("""
        create temp table s_run_log as
        select * from src.raw.run_log
        where run_id = ? or (step = 'sodra' and run_month <= ?)""", [run_id, run_month])
    # Sodra rows only for the 9-digit codes on this run's own pages (int_page_code_hits uses
    # the same regex), so the snapshot stays small and matching sees the same candidates.
    con.execute("""
        create temp table s_sodra as
        select * replace (regexp_replace(source_url, '^file://.*/', 'file:') as source_url)
        from src.raw.sodra
        where run_id in (select run_id from s_run_log where step = 'sodra')
          and jarCode in (select unnest(regexp_extract_all(markdown, '\\b\\d{9}\\b'))
                          from s_web_pages where markdown is not null)""")


def write_snapshot(db_path: Path, run_id: str | None = None, out_dir: Path | None = None,
                   force: bool = False) -> Path:
    db_path = Path(db_path)
    if not db_path.exists():
        raise SnapshotError(f"no database at {db_path}")
    con = duckdb.connect()
    try:
        con.execute(f"attach {_q(str(db_path))} as src (read_only)")
        run_id = run_id or latest_complete_run(con)
        if run_id is None:
            raise SnapshotError("no run with both places and crawl in raw.run_log")
        row = con.execute("select max(run_month) from src.raw.run_log where run_id = ?",
                          [run_id]).fetchone()
        if row[0] is None:
            raise SnapshotError(f"run {run_id} not in raw.run_log")
        run_month = row[0]

        out = Path(out_dir) if out_dir is not None else SNAPSHOT_ROOT / run_id
        if out.exists() and any(out.iterdir()) and not force:
            raise SnapshotError(f"{out} is not empty (use --force to overwrite)")
        out.mkdir(parents=True, exist_ok=True)

        _select(con, run_id, run_month)
        counts = {}
        for t in TABLES:
            con.execute(f"copy s_{t} to {_q(str(out / f'{t}.parquet'))} (format parquet)")
            counts[f"{t}.parquet"] = con.execute(f"select count(*) from s_{t}").fetchone()[0]
        sodra_url = con.execute("select any_value(source_url) from s_sodra").fetchone()[0]
    finally:
        con.close()

    manifest = {
        "run_id": run_id,
        "run_month": run_month,
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "git_commit": _git_commit(),
        "rows": counts,
        "sodra_source_url": sodra_url,
        "note": NOTE,
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return out
