"""A dbt-built lead DB whose run has places, crawl and sodra rows (for snapshot/demo tests)."""
from pathlib import Path

from src.db import connect, insert_rows
from src.run import new_run
from tests.dbt_harness import build, load_lead_demo, sodra

RUN = new_run("2026-10")


def built_lead_db(db: Path) -> Path:
    con = connect(db)
    load_lead_demo(con, RUN)
    insert_rows(con, "sodra", [sodra("999999999", name="UAB Unrelated")], RUN)
    insert_rows(con, "run_log", [{
        "run_month": RUN.run_month, "step": "crawl",
        "started_at": RUN.started_at.replace(tzinfo=None),
        "finished_at": RUN.started_at.replace(tzinfo=None), "status": "ok",
        "items_ok": 7, "items_failed": 0, "api_requests": 7, "cache_hits": 0, "notes": None,
    }], RUN)
    con.close()
    build(db)
    return db
