"""Run commands: full (live, within the free tiers), snapshot (save a run), demo (offline replay).

    python -m src.cli full [--dry-run]
    python -m src.cli snapshot [--run-id ID] [--out DIR] [--force]
    python -m src.cli demo [--snapshot DIR] [--out DIR] [--top N] [--db PATH]
"""
import argparse
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

from src.budget import format_budgets, plan_budgets, used_this_month
from src.settings import load_config, load_settings_or_exit


def cmd_full(dry_run: bool) -> int:
    from src import pipeline
    from src.extract.places import worst_case_requests

    settings = load_settings_or_exit()
    budgets = plan_budgets(settings, used_this_month(settings.db_path, datetime.now(UTC)))
    worst = worst_case_requests(load_config(settings.config_path))
    if dry_run:
        print("\n".join(format_budgets(budgets)))
        print(f"places worst case: {worst} requests for {settings.config_path}")
        return 0
    exhausted = [(api, b) for api, b in budgets.items() if b.cap <= 0]
    for api, b in exhausted:
        print(f"full: {api} free tier used up this month ({b.used} of {b.free})", file=sys.stderr)
    if exhausted:
        return 2
    if worst > budgets["places"].cap:
        print(f"full: places worst case {worst} requests exceeds the run cap "
              f"{budgets['places'].cap}; shrink area.bbox or raise area.min_tile_km in "
              f"{settings.config_path}", file=sys.stderr)
        return 2
    capped = settings.model_copy(update={"max_places_requests": budgets["places"].cap,
                                         "max_firecrawl_pages": budgets["firecrawl"].cap})
    code, run_id = pipeline.run(capped)
    if code != 0:
        return code
    return _snapshot(settings.db_path, run_id, None, False)


def _snapshot(db_path: Path, run_id: str | None, out: Path | None, force: bool) -> int:
    from src.snapshot import SnapshotError, write_snapshot

    try:
        print(write_snapshot(db_path, run_id, out, force))
    except SnapshotError as exc:
        print(f"snapshot: {exc}", file=sys.stderr)
        return 1
    return 0


def cmd_snapshot(run_id: str | None, out: Path | None, force: bool) -> int:
    return _snapshot(load_settings_or_exit().db_path, run_id, out, force)


def cmd_demo(snapshot: Path | None, out: Path, top: int | None, db: Path) -> int:
    from src.demo import DemoError, newest_snapshot, run_demo

    top_n = top if top is not None else int(os.environ.get("LEADS_TOP_N", 10))
    try:
        path = run_demo(snapshot or newest_snapshot(), db, out, top_n)
    except DemoError as exc:
        print(f"demo: {exc}", file=sys.stderr)
        return 1
    print(path)
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m src.cli")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p_full = sub.add_parser("full", help="live run within the free tiers, then snapshot it")
    p_full.add_argument("--dry-run", action="store_true", help="print the budget and stop")
    p_snap = sub.add_parser("snapshot", help="save one run's raw rows as parquet (no API calls)")
    p_snap.add_argument("--run-id", help="default: latest run with places and crawl")
    p_snap.add_argument("--out", type=Path, help="default: snapshots/<run_id>")
    p_snap.add_argument("--force", action="store_true", help="overwrite a non-empty --out")
    p_demo = sub.add_parser("demo", help="rebuild the leads from a snapshot, offline")
    p_demo.add_argument("--snapshot", type=Path, help="default: newest under snapshots/")
    p_demo.add_argument("--out", type=Path, default=Path("output"))
    p_demo.add_argument("--top", type=int, help="default: LEADS_TOP_N (10)")
    p_demo.add_argument("--db", type=Path, default=Path("data/demo.duckdb"))
    args = ap.parse_args(argv)

    if args.cmd == "full":
        return cmd_full(args.dry_run)
    if args.cmd == "snapshot":
        return cmd_snapshot(args.run_id, args.out, args.force)
    return cmd_demo(args.snapshot, args.out, args.top, args.db)


if __name__ == "__main__":
    sys.exit(main())
