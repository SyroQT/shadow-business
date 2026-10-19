"""Build the example extract report from recorded fixtures (offline, no live run).

Replays tests/fixtures/recorded/ through extract_all into a throwaway DuckDB, then
writes output/demo-recorded/extract_report.md. Usage: uv run python scripts/demo_report.py
"""
import os
import sys
import tempfile
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.cache import SourceClient, TokenBucket  # noqa: E402
from src.db import connect  # noqa: E402
from src.extract.__main__ import extract_all  # noqa: E402
from src.extract.report import write_report  # noqa: E402
from src.run import new_run  # noqa: E402
from src.settings import Settings  # noqa: E402
from tests.replay import replay_transport  # noqa: E402
from tests.test_extract_all import mk, robots_handler  # noqa: E402
from tests.test_places import config  # noqa: E402
from tests.test_sodra_load import make_zip  # noqa: E402

BANNER = ("> Demo report generated from recorded fixtures (tests/fixtures/recorded/), "
          "not a live run.\n"
          "> Crawl rows for sites outside the 10 recorded homepages are synthetic "
          "\"not recorded\" errors, so the crawl share and error counts are not real coverage.")


def rc(source, transport, **kw):
    return SourceClient(source, None, TokenBucket(1000, sleep=lambda s: None), None, {},
                        transport=transport, sleep=lambda s: None, **kw)


def main() -> int:
    if not any((ROOT / "tests/fixtures/recorded").rglob("*.json")):
        print("no recorded fixtures in tests/fixtures/recorded/", file=sys.stderr)
        return 2
    out_root = ROOT / "output"
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        os.chdir(tmp)  # run_sodra lands the zip under ./data/raw/sodra
        cfg = config({"cafes_restaurants": ["cafe", "restaurant"]}, tile_km=1.0, min_tile_km=1.0)
        cfg.crawl.legal_link_pattern = "(?!x)x"  # legal pages were not recorded
        settings = Settings(google_api_key="g", firecrawl_api_key="f", _env_file=None,
                            sodra_file=make_zip(tmp), max_firecrawl_pages=1000)
        clients = {
            "sodra": mk(robots_handler),
            "places": rc("places", replay_transport("places")),
            "firecrawl": rc("firecrawl", replay_transport("firecrawl", fallback=lambda r: (
                httpx.Response(200, json={"success": False, "error": "not recorded"})))),
            "robots": rc("robots", replay_transport("robots", fallback=robots_handler),
                         follow_redirects=True),
        }
        con = connect(tmp / "demo.duckdb")
        run = new_run("2026-10")
        extract_all(settings, cfg, con, run, clients, sodra_min_rows=10)
        path = write_report(con, run.run_id, out_root=out_root, dir_name="demo-recorded")
        con.close()
    path.write_text(BANNER + "\n\n" + path.read_text(), encoding="utf-8")
    print(path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
