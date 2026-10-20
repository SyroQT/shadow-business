"""Helpers for dbt tests: raw-row builders, a loader, and build/query wrappers."""
import subprocess
from pathlib import Path

import duckdb

from src.db import insert_rows
from src.run import RunContext
from src.transform import run_dbt


def place(place_id, category, name, *, website=None, user_rating_count=None,
          rating=None, primary_type="cafe") -> dict:
    payload = {
        "id": place_id,
        "displayName": {"text": name, "languageCode": "lt"},
        "primaryType": primary_type,
        "types": [primary_type, "food", "establishment"],
        "formattedAddress": "Gedimino pr. 1, Vilnius, Lithuania",
        "location": {"latitude": 54.687, "longitude": 25.279},
        "businessStatus": "OPERATIONAL",
        "googleMapsUri": f"https://maps.google.com/?cid={place_id}",
    }
    if website is not None:
        payload["websiteUri"] = website
    if user_rating_count is not None:
        payload["userRatingCount"] = user_rating_count
    if rating is not None:
        payload["rating"] = rating
    return {
        "category": category, "tile_id": "t0", "request_hash": f"h-{place_id}",
        "place_id": place_id, "payload": payload,
    }


def page(place_id, role, markdown, *, skip_reason=None, url="https://x.lt/") -> dict:
    return {
        "place_id": place_id, "page_role": role, "requested_url": url,
        "final_url": url, "http_status": None if skip_reason else 200,
        "skip_reason": skip_reason, "markdown": markdown, "links": [],
        "content_sha256": None if markdown is None else "sha-" + str(abs(hash(markdown))),
    }


def sodra(jar_code, *, month="202608", evrk="56.11.00", num_insured="3",
          avg_wage="1500.00", tax="900.00", name="UAB X") -> dict:
    return {
        "source_url": "file:///s.zip", "file_sha256": "abc", "data_month": month,
        "code": jar_code, "jarCode": jar_code, "name": name,
        "municipality": "Vilniaus m. sav.", "ecoActCode": evrk,
        "ecoActCodeStr": evrk, "ecoActName": "Activity", "month": month,
        "avgWage": avg_wage, "numInsured": num_insured, "avgWage2": avg_wage,
        "numInsured2": num_insured, "tax": tax,
    }


def load(con, run: RunContext, *, places=(), pages=(), sodra=(),
         steps={"places": "ok"}) -> None:  # noqa: B006 - never mutated
    insert_rows(con, "places", list(places), run)
    insert_rows(con, "web_pages", list(pages), run)
    insert_rows(con, "sodra", list(sodra), run)
    insert_rows(con, "run_log", [
        {
            "run_month": run.run_month, "step": step,
            "started_at": run.started_at.replace(tzinfo=None),
            "finished_at": run.started_at.replace(tzinfo=None),
            "status": status, "items_ok": 0, "items_failed": 0,
            "api_requests": 0, "cache_hits": 0, "notes": None,
        }
        for step, status in steps.items()
    ], run)


def build(db_path) -> subprocess.CompletedProcess[str]:
    res = run_dbt(["build"], Path(db_path))
    assert res.returncode == 0, res.stdout + res.stderr
    return res


def rows(db_path, sql) -> list[tuple]:
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        return con.execute(sql).fetchall()
    finally:
        con.close()
