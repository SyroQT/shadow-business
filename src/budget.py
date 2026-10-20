"""Free-tier budget: API requests already spent this calendar month (UTC), from raw.run_log."""
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import duckdb

from src.settings import Settings

STEPS = ("places", "crawl")


def _month_bounds(now: datetime) -> tuple[datetime, datetime]:
    start = datetime(now.year, now.month, 1)
    end = datetime(now.year + (now.month == 12), now.month % 12 + 1, 1)
    return start, end


def used_this_month(db_path: Path, now: datetime) -> dict[str, int]:
    """Sum of run_log.api_requests per step for steps started this month; zeros without a DB."""
    used = dict.fromkeys(STEPS, 0)
    if not Path(db_path).exists():
        return used
    start, end = _month_bounds(now)
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        exists = con.execute("select count(*) from information_schema.tables "
                             "where table_schema = 'raw' and table_name = 'run_log'").fetchone()[0]
        if not exists:
            return used
        for step, n in con.execute(
            "select step, coalesce(sum(api_requests), 0) from raw.run_log "
            "where step in ('places', 'crawl') and started_at >= ? and started_at < ? group by 1",
            [start, end],
        ).fetchall():
            used[step] = int(n)
    finally:
        con.close()
    return used


@dataclass(frozen=True)
class Caps:
    places_used: int
    places_free: int
    places_remaining: int
    places_cap: int
    firecrawl_used: int
    firecrawl_free: int
    firecrawl_remaining: int
    firecrawl_cap: int

    @property
    def exhausted(self) -> list[str]:
        return [api for api in ("places", "firecrawl") if getattr(self, f"{api}_remaining") <= 0]


def plan_caps(settings: Settings, used: dict[str, int]) -> Caps:
    p_rem = max(settings.places_free_tier - used["places"], 0)
    f_rem = max(settings.firecrawl_free_tier - used["crawl"], 0)
    return Caps(used["places"], settings.places_free_tier, p_rem,
                min(settings.max_places_requests, p_rem),
                used["crawl"], settings.firecrawl_free_tier, f_rem,
                min(settings.max_firecrawl_pages, f_rem))


def format_caps(c: Caps) -> list[str]:
    return [
        f"{api}: used {getattr(c, f'{api}_used')} of {getattr(c, f'{api}_free')} this month, "
        f"remaining {getattr(c, f'{api}_remaining')}, run cap {getattr(c, f'{api}_cap')}"
        for api in ("places", "firecrawl")
    ]
