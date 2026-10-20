"""Free-tier budget: API requests already spent this calendar month (UTC), from raw.run_log."""
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import duckdb

from src.settings import Settings


@dataclass(frozen=True)
class Budget:
    used: int
    free: int
    cap: int  # requests this run may make: min(MAX_*, free - used), at least 0


def _month_bounds(now: datetime) -> tuple[datetime, datetime]:
    start = datetime(now.year, now.month, 1)
    end = datetime(now.year + (now.month == 12), now.month % 12 + 1, 1)
    return start, end


def used_this_month(db_path: Path, now: datetime) -> dict[str, int]:
    """API requests per API (places, firecrawl) for steps started this month; zeros without a DB."""
    used = {"places": 0, "firecrawl": 0}
    if not Path(db_path).exists():
        return used
    start, end = _month_bounds(now)
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        if not con.execute("select count(*) from information_schema.tables "
                           "where table_schema = 'raw' and table_name = 'run_log'").fetchone()[0]:
            return used
        for api, n in con.execute(
            "select case step when 'crawl' then 'firecrawl' else step end, "
            "coalesce(sum(api_requests), 0) from raw.run_log "
            "where step in ('places', 'crawl') and started_at >= ? and started_at < ? group by 1",
            [start, end],
        ).fetchall():
            used[api] = int(n)
    finally:
        con.close()
    return used


def plan_budgets(settings: Settings, used: dict[str, int]) -> dict[str, Budget]:
    limits = {"places": (settings.places_free_tier, settings.max_places_requests),
              "firecrawl": (settings.firecrawl_free_tier, settings.max_firecrawl_pages)}
    return {api: Budget(used[api], free, max(min(per_run, free - used[api]), 0))
            for api, (free, per_run) in limits.items()}


def format_budgets(budgets: dict[str, Budget]) -> list[str]:
    return [f"{api}: used {b.used} of {b.free} this month, "
            f"remaining {max(b.free - b.used, 0)}, run cap {b.cap}"
            for api, b in budgets.items()]
