"""Load the recorded API calls into the real DuckDB as one demo run (no network).

    uv run python scripts/load_recorded_demo.py                # recorded places + recorded crawl
    uv run python scripts/load_recorded_demo.py --places-only  # recorded places only; then crawl live:
    uv run python -m src.extract crawl --run-id <printed run_id>

Replays tests/fixtures/recorded/ (10 Places pages, 10 Firecrawl homepages, 10 robots.txt)
through the real places + crawl steps under one new run_id. Each category is searched on
the demo tile it was recorded on. Requests outside the recordings get an explicit
"not recorded (demo replay)" answer, so they land as error rows instead of real data.
Sodra is not touched (already landed).
"""

import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.cache import SourceClient, TokenBucket  # noqa: E402
from src.db import connect, step  # noqa: E402
from src.extract.crawl import run_crawl  # noqa: E402
from src.extract.places import _run_work  # noqa: E402
from src.extract.report import build_report  # noqa: E402
from src.extract.tiles import Tile  # noqa: E402
from src.run import new_run  # noqa: E402
from src.settings import load_config, load_settings_or_exit  # noqa: E402
from tests.replay import RECORDED, replay_transport  # noqa: E402

NOT_RECORDED = "not recorded (demo replay)"

# category -> (included types, the tile they were recorded on)
DEMO_TILES = {
    "cafes_restaurants": (["cafe", "restaurant"],
                          Tile("demo_oldtown_1km", 54.679, 25.276, 54.688, 25.291, 1.0)),
    "hair_beauty": (["hair_salon"],
                    Tile("demo_centre_2km", 54.678, 25.260, 54.696, 25.291, 2.0)),
    "car_service": (["car_repair"],
                    Tile("demo_naujamiestis_2km", 54.665, 25.235, 54.683, 25.266, 2.0)),
}


def _client(source: str, fallback) -> SourceClient:
    """No disk cache (keeps data/cache clean), no sleeping, replay-only transport."""
    return SourceClient(source, cache=None, limiter=TokenBucket(1e9), max_requests=None,
                        headers={}, transport=replay_transport(source, fallback=fallback),
                        sleep=lambda _s: None)


def main(argv: list[str] | None = None) -> int:
    places_only = "--places-only" in (sys.argv[1:] if argv is None else argv)
    if not any(RECORDED.rglob("*.json")):
        sys.exit("no recordings in tests/fixtures/recorded/ — run scripts/record_fixtures.py first")
    settings = load_settings_or_exit()
    config = load_config(settings.config_path)
    # Saturated demo tiles become coverage gaps instead of splitting into unrecorded children.
    config = config.model_copy(update={
        "area": config.area.model_copy(update={"min_tile_km": 1.0}),
        "crawl": config.crawl.model_copy(update={"legal_link_pattern": r"(?!x)x"}),  # legal pages weren't recorded
    })

    places = _client("places", lambda r: httpx.Response(404, json={"error": NOT_RECORDED}))
    firecrawl = _client("firecrawl", lambda r: httpx.Response(
        200, json={"success": False, "error": NOT_RECORDED}))
    robots = _client("robots", lambda r: httpx.Response(404, text=""))

    run = new_run(settings.run_month)
    con = connect(settings.db_path)
    try:
        with step(con, run, "places") as stats:
            try:
                for cat, (types, tile) in DEMO_TILES.items():
                    _run_work(con, places, config, run, stats, [cat],
                              [(cat, t) for t in types], [tile])
            finally:
                stats.api_requests, stats.cache_hits = places.api_requests, places.cache_hits
            stats.notes.append("demo_replay")
        if places_only:
            print(f"loaded recorded places as run {run.run_id} into {settings.db_path}")
            print(f"next (live Firecrawl, costs credits): uv run python -m src.extract crawl --run-id {run.run_id}")
            return 0
        run_crawl(con, firecrawl, robots, config, run, settings.max_firecrawl_pages)
        print(build_report(con, run.run_id))
        print(f"\nloaded demo run {run.run_id} into {settings.db_path}")
    finally:
        con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
