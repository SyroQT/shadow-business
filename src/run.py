"""Run identity shared by all steps."""
import re
from dataclasses import dataclass
from datetime import UTC, datetime

_MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


@dataclass(frozen=True)
class RunContext:
    run_id: str
    run_month: str
    started_at: datetime


def new_run(run_month: str | None = None, now: datetime | None = None) -> RunContext:
    now = now or datetime.now(UTC)
    month = run_month if run_month is not None else now.strftime("%Y-%m")
    if not _MONTH_RE.match(month):
        raise ValueError(f"run_month must be YYYY-MM, got {month!r}")
    return RunContext(f"{month}_{now.strftime('%Y%m%dT%H%M%S')}", month, now)
