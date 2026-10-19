"""DuckDB raw schema and append-only writer.

All timestamps are naive UTC stored in TIMESTAMP columns. No UPDATE or DELETE is
ever issued; every step writes exactly one run_log row when it finishes.
"""
import json
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import duckdb

from src.log import log
from src.run import RunContext

_SODRA_COLS = [
    "code", "jarCode", "name", "municipality", "ecoActCode", "ecoActCodeStr",
    "ecoActName", "month", "avgWage", "numInsured", "avgWage2", "numInsured2", "tax",
]

_STAMP = "run_id VARCHAR, retrieved_at TIMESTAMP"

_DDL = [
    "CREATE SCHEMA IF NOT EXISTS raw",
    """CREATE TABLE IF NOT EXISTS raw.run_log (
        run_id VARCHAR, run_month VARCHAR, step VARCHAR,
        started_at TIMESTAMP, finished_at TIMESTAMP, status VARCHAR,
        items_ok INTEGER, items_failed INTEGER, api_requests INTEGER,
        cache_hits INTEGER, notes VARCHAR)""",
    f"""CREATE TABLE IF NOT EXISTS raw.places (
        {_STAMP}, category VARCHAR, tile_id VARCHAR, request_hash VARCHAR,
        place_id VARCHAR, payload JSON)""",
    f"""CREATE TABLE IF NOT EXISTS raw.places_errors (
        {_STAMP}, category VARCHAR, tile_id VARCHAR, request_hash VARCHAR,
        http_status INTEGER, error VARCHAR)""",
    f"""CREATE TABLE IF NOT EXISTS raw.web_pages (
        {_STAMP}, place_id VARCHAR, page_role VARCHAR, requested_url VARCHAR,
        final_url VARCHAR, http_status INTEGER, skip_reason VARCHAR,
        markdown VARCHAR, links JSON, content_sha256 VARCHAR)""",
    f"""CREATE TABLE IF NOT EXISTS raw.sodra (
        {_STAMP}, source_url VARCHAR, file_sha256 VARCHAR, data_month VARCHAR,
        {", ".join(f'"{c}" VARCHAR' for c in _SODRA_COLS)})""",
]

# run_log has run_id but no retrieved_at.
_NO_RETRIEVED_AT = {"run_log"}


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def ensure_schema(con: duckdb.DuckDBPyConnection) -> None:
    for stmt in _DDL:
        con.execute(stmt)


def connect(path: Path) -> duckdb.DuckDBPyConnection:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(path))
    ensure_schema(con)
    return con


def _val(v):
    if isinstance(v, datetime) and v.tzinfo is not None:
        return v.astimezone(UTC).replace(tzinfo=None)
    return json.dumps(v) if isinstance(v, (dict, list)) else v


def insert_rows(
    con: duckdb.DuckDBPyConnection,
    table: str,
    rows: list[dict],
    run: RunContext,
    retrieved_at: datetime | None = None,
) -> int:
    if not rows:
        return 0
    ts = retrieved_at or _utcnow()
    if ts.tzinfo is not None:
        ts = ts.astimezone(UTC).replace(tzinfo=None)
    cols = list(rows[0])
    stamps = {"run_id": run.run_id}
    if table not in _NO_RETRIEVED_AT:
        stamps["retrieved_at"] = ts
    extra = [c for c in stamps if c not in cols]
    all_cols = cols + extra
    data = []
    for r in rows:
        merged = {**stamps, **r}
        data.append([_val(merged[c]) for c in all_cols])
    sql = (
        f"INSERT INTO raw.{table} ({', '.join(f'"{c}"' for c in all_cols)}) "
        f"VALUES ({', '.join('?' * len(all_cols))})"
    )
    con.executemany(sql, data)
    return len(data)


@dataclass
class StepStats:
    items_ok: int = 0
    items_failed: int = 0
    api_requests: int = 0
    cache_hits: int = 0
    status: str | None = None
    notes: list[str] = field(default_factory=list)


@contextmanager
def step(con: duckdb.DuckDBPyConnection, run: RunContext, name: str) -> Iterator[StepStats]:
    stats = StepStats()
    started = _utcnow()
    t0 = time.monotonic()
    error: BaseException | None = None
    try:
        yield stats
    except BaseException as exc:
        error = exc
        stats.notes.append(f"{type(exc).__name__}: {exc}")
        raise
    finally:
        if error is not None:
            status = "failed"
        elif stats.status:
            status = stats.status
        elif stats.items_failed > 0:
            status = "partial"
        else:
            status = "ok"
        notes = "; ".join(stats.notes) or None
        insert_rows(
            con,
            "run_log",
            [{
                "run_month": run.run_month, "step": name, "started_at": started,
                "finished_at": _utcnow(), "status": status,
                "items_ok": stats.items_ok, "items_failed": stats.items_failed,
                "api_requests": stats.api_requests, "cache_hits": stats.cache_hits,
                "notes": notes,
            }],
            run,
        )
        log(
            "step_finished", step=name, run_id=run.run_id, status=status,
            items_ok=stats.items_ok, items_failed=stats.items_failed,
            api_requests=stats.api_requests, cache_hits=stats.cache_hits,
            duration_ms=int((time.monotonic() - t0) * 1000),
        )
