"""Orchestrated extract entrypoint: python -m src.extract {all,sodra,places,crawl,report}."""
import argparse
import sys

import duckdb

from src.cache import (
    MissingSetting, SourceClient, firecrawl_client, places_client, robots_client, sodra_client,
)
from src.db import connect, step
from src.extract.crawl import run_crawl
from src.extract.places import run_places
from src.extract.sodra import run_sodra
from src.log import log
from src.run import RunContext, new_run
from src.settings import AppConfig, Settings, load_config, load_settings_or_exit

STEPS = ("sodra", "places", "crawl")


def _status(con: duckdb.DuckDBPyConnection, run_id: str, name: str) -> str:
    """Latest run_log status for the step (the source of truth); failed if absent."""
    row = con.execute(
        "select status from raw.run_log where run_id = ? and step = ?"
        " order by finished_at desc limit 1", [run_id, name]).fetchone()
    return row[0] if row else "failed"


def _guarded(name: str, run: RunContext, fn) -> None:
    """Run a step; step() already recorded `failed` if it raises, so log and carry on."""
    try:
        fn()
    except Exception as exc:
        log("error", step=name, run_id=run.run_id, error=type(exc).__name__,
            message=str(exc)[:200])


def build_clients(settings: Settings) -> dict[str, SourceClient]:
    """Keyless clients only; the keyed ones (places, firecrawl) are built when their step runs."""
    return {"sodra": sodra_client(settings), "robots": robots_client(settings)}


def _missing_setting(con, run: RunContext, name: str, setting: str) -> None:
    """R26: record the step as failed through step() with note missing_setting=<NAME>."""
    with step(con, run, name) as stats:
        stats.status = "failed"
        stats.notes.append(f"missing_setting={setting}")
    log("error", step=name, run_id=run.run_id, error="MissingSetting",
        message=f"{setting} is not set")


def _warn(statuses: dict[str, str], run: RunContext) -> None:
    for name, status in statuses.items():
        if status in ("capped", "partial"):
            log("warning", step=name, run_id=run.run_id, message=f"step finished {status}")


def _crawl(settings, config, con, run, clients) -> None:
    try:
        firecrawl = clients.get("firecrawl") or firecrawl_client(settings)
    except MissingSetting as e:
        _missing_setting(con, run, "crawl", e.name)
        return
    robots = clients.get("robots") or robots_client(settings)
    _guarded("crawl", run, lambda: run_crawl(
        con, firecrawl, robots, config, run, settings.max_firecrawl_pages))


def extract_all(settings: Settings, config: AppConfig, con: duckdb.DuckDBPyConnection,
                run: RunContext, clients: dict[str, SourceClient], *,
                sodra_min_rows: int = 50_000) -> dict[str, str]:
    """sodra -> places -> crawl under one run_id; returns step -> status from raw.run_log."""
    _guarded("sodra", run, lambda: run_sodra(
        con, settings, clients.get("sodra"), run, min_rows_per_month=sodra_min_rows))
    try:
        places = clients.get("places") or places_client(settings)
    except MissingSetting as e:
        _missing_setting(con, run, "places", e.name)
    else:
        _guarded("places", run, lambda: run_places(con, places, config, run))
    if _status(con, run.run_id, "places") == "failed":
        with step(con, run, "crawl") as stats:
            stats.status = "failed"
            stats.notes.append("upstream_places_failed")
        log("error", step="crawl", run_id=run.run_id, error="UpstreamFailed",
            message="upstream_places_failed")
    else:
        _crawl(settings, config, con, run, clients)
    statuses = {name: _status(con, run.run_id, name) for name in STEPS}
    _warn(statuses, run)
    return statuses


def _exit_code(statuses: dict[str, str]) -> int:
    return 1 if "failed" in statuses.values() else 0


def extract_run(settings: Settings) -> tuple[int, str]:
    """`extract all` with the given settings; returns (exit code, run_id)."""
    config = load_config(settings.config_path)
    con = connect(settings.db_path)
    try:
        run = new_run(settings.run_month)
        return _exit_code(extract_all(settings, config, con, run, build_clients(settings))), run.run_id
    finally:
        con.close()


def _latest_places_run(con: duckdb.DuckDBPyConnection, run_month: str) -> str | None:
    row = con.execute(
        "select run_id from raw.run_log where step = 'places' and run_month = ?"
        " order by finished_at desc limit 1", [run_month]).fetchone()
    return row[0] if row else None


def _run_for_id(run_id: str) -> RunContext:
    month = run_id.split("_")[0]
    return RunContext(run_id, month, new_run(month).started_at)


def _report(run_id: str | None) -> int:
    from src.extract.report import latest_run_id, write_report
    settings = load_settings_or_exit()
    con = connect(settings.db_path)
    try:
        if run_id is not None and con.execute(
                "select 1 from raw.run_log where run_id = ? limit 1", [run_id]).fetchone() is None:
            print(f"report: unknown run id {run_id}", file=sys.stderr)
            return 2
        if run_id is None:
            month = new_run(settings.run_month).run_month
            run_id = latest_run_id(con, month)
            if run_id is None:
                print(f"report: no run found for {month}; pass --run-id", file=sys.stderr)
                return 2
        print(write_report(con, run_id))
        return 0
    finally:
        con.close()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m src.extract")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("all", help="sodra, places, crawl under one run_id")
    sub.add_parser("sodra", help="Sodra monthly file")
    p_places = sub.add_parser("places", help="Google Places search")
    p_places.add_argument("--category", action="append")
    p_places.add_argument("--run-id")
    p_crawl = sub.add_parser("crawl", help="Firecrawl homepages and legal pages")
    p_crawl.add_argument("--run-id", help="default: latest places run for RUN_MONTH")
    p_report = sub.add_parser("report", help="data-quality report from raw.* (offline)")
    p_report.add_argument("--run-id", help="default: latest run for RUN_MONTH")
    args = ap.parse_args(argv)

    if args.cmd == "report":
        return _report(args.run_id)

    if args.cmd == "sodra":
        from src.extract import sodra
        return sodra.main()
    if args.cmd == "places":
        from src.extract import places
        fwd = [a for c in args.category or [] for a in ("--category", c)]
        if args.run_id:
            fwd += ["--run-id", args.run_id]
        return places.main(fwd)

    settings = load_settings_or_exit()
    if args.cmd == "all":
        return extract_run(settings)[0]
    config = load_config(settings.config_path)
    con = connect(settings.db_path)
    try:
        # crawl
        if args.run_id:
            run = _run_for_id(args.run_id)
        else:
            month = new_run(settings.run_month).run_month
            run_id = _latest_places_run(con, month)
            if run_id is None:
                print(f"crawl: no places run found for {month}; run places first or pass --run-id",
                      file=sys.stderr)
                return 2
            run = _run_for_id(run_id)
        _crawl(settings, config, con, run, {})
        statuses = {"crawl": _status(con, run.run_id, "crawl")}
        _warn(statuses, run)
        return _exit_code(statuses)
    finally:
        con.close()


if __name__ == "__main__":
    sys.exit(main())
