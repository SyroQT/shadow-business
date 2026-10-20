"""Places API (Text Search New) -> raw.places via a per-type quadtree of tiles."""
from dataclasses import dataclass, field
from datetime import datetime

import argparse
import re
import sys

import duckdb

from src.cache import ApiError, CapReached, MissingSetting, SourceClient, places_client, request_hash
from src.db import StepStats, connect, insert_rows, step
from src.extract.tiles import Tile, root_tiles, split
from src.log import log
from src.run import RunContext, new_run
from src.settings import AppConfig, load_config, load_settings_or_exit

SEARCH_URL = "https://places.googleapis.com/v1/places:searchText"
FIELD_MASK = ",".join([
    "places.id", "places.displayName", "places.formattedAddress", "places.location",
    "places.types", "places.primaryType", "places.websiteUri", "places.userRatingCount",
    "places.rating", "places.priceLevel", "places.regularOpeningHours",
    "places.businessStatus", "places.googleMapsUri", "nextPageToken",
])
PAGE_SIZE = 20
MAX_PAGES = 3
MAX_RESULTS = PAGE_SIZE * MAX_PAGES


def build_body(included_type: str, tile: Tile, page_token: str | None) -> dict:
    body = {
        "textQuery": included_type.replace("_", " "),
        "includedType": included_type,
        "strictTypeFiltering": True,
        "pageSize": PAGE_SIZE,
        "languageCode": "lt",
        "regionCode": "LT",
        "locationRestriction": {"rectangle": {
            "low": {"latitude": tile.south, "longitude": tile.west},
            "high": {"latitude": tile.north, "longitude": tile.east},
        }},
    }
    if page_token:
        body["pageToken"] = page_token
    return body


@dataclass
class TileResult:
    places: list[tuple[str, dict, str, datetime]] = field(default_factory=list)
    saturated: bool = False
    error: tuple[int | None, str, str] | None = None  # (http_status, message, request_hash)


class _PageFailed(Exception):
    def __init__(self, page: int, error: ApiError, req_hash: str, places: list):
        super().__init__(str(error))
        self.page, self.error, self.req_hash, self.places = page, error, req_hash, places


class CapWithPartial(CapReached):
    """CapReached that carries the places from pages already fetched for the tile."""

    def __init__(self, message: str, places: list):
        super().__init__(message)
        self.places = places


def _fetch_chain(client: SourceClient, included_type: str, tile: Tile, use_cache: bool):
    places: list[tuple[str, dict, str, datetime]] = []
    token = None
    for page in range(MAX_PAGES):
        body = build_body(included_type, tile, token)
        rh = request_hash("POST", SEARCH_URL, body)
        try:
            resp = client.request("POST", SEARCH_URL, body, use_cache=use_cache,
                                  headers={"X-Goog-FieldMask": FIELD_MASK})
        except ApiError as e:
            raise _PageFailed(page, e, rh, places) from None
        except CapReached as e:
            raise CapWithPartial(str(e), places) from None
        data = resp.json()
        for p in data.get("places", []):
            places.append((p["id"], p, rh, resp.fetched_at))
        token = data.get("nextPageToken")
        if not token:
            break
    return places


def search_tile(client: SourceClient, included_type: str, tile: Tile) -> TileResult:
    """Page through one (type, tile). AuthError and CapReached propagate."""
    try:
        try:
            places = _fetch_chain(client, included_type, tile, use_cache=True)
        except _PageFailed as e:
            if e.page == 0 or e.error.status != 400:
                raise
            # Stale pageToken (e.g. cached page 1 outlived its tokens): refetch the chain.
            places = _fetch_chain(client, included_type, tile, use_cache=False)
    except _PageFailed as e:
        return TileResult(places=e.places, error=(e.error.status, e.error.message, e.req_hash))
    return TileResult(places=places, saturated=len(places) >= MAX_RESULTS)


def worst_case_requests(config: AppConfig, categories: list[str] | None = None) -> int:
    """Upper bound on Places requests for one run: every tile saturates and splits down to
    min_tile_km, each tile using all MAX_PAGES pages. (A stale-pageToken refetch can add a
    chain in rare cases; the per-category cap still holds.)"""
    area = config.area
    levels, size = 1, area.tile_km
    while size / 2 >= area.min_tile_km:
        size /= 2
        levels += 1
    tiles_per_root = (4 ** levels - 1) // 3
    names = categories if categories is not None else list(config.categories)
    types = sum(len(config.categories[c].places_included_type) for c in names)
    return len(root_tiles(area.bbox, area.tile_km)) * types * tiles_per_root * MAX_PAGES


def run_places(
    con: duckdb.DuckDBPyConnection,
    client: SourceClient,
    config: AppConfig,
    run: RunContext,
    categories: list[str] | None = None,
) -> StepStats:
    names = sorted(categories if categories is not None else config.categories)
    area = config.area
    roots = root_tiles(area.bbox, area.tile_km)
    work = [(c, t) for c in names for t in config.categories[c].places_included_type]

    with step(con, run, "places") as stats:
        try:
            _run_work(con, client, config, run, stats, names, work, roots)
        finally:
            stats.api_requests = client.api_requests
            stats.cache_hits = client.cache_hits
    return stats


def _run_work(con, client, config, run, stats, names, work, roots) -> None:
    """Categories in turn; with a request cap, each gets an equal share of what is left, so a
    dense category cannot starve the next ones (unused share carries forward)."""
    cap = client.max_requests
    try:
        for i, cat in enumerate(names):
            if cap is not None:
                left = cap - client.api_requests
                client.max_requests = client.api_requests + -(-left // (len(names) - i))
            _run_category(con, client, config, run, stats, cat, work, roots)
    finally:
        client.max_requests = cap


def _run_category(con, client, config, run, stats, cat, work, roots) -> None:
    min_km = config.area.min_tile_km
    seen: set[str] = set()
    rows: list[dict] = []
    errors: list[dict] = []
    saturated = gaps = 0
    capped_at: int | None = None

    def add(tile, places, seen=seen, rows=rows, cat=cat):
        for pid, place, rh, at in places:
            if pid in seen:
                continue
            seen.add(pid)
            rows.append({"category": cat, "tile_id": tile.tile_id, "request_hash": rh,
                         "place_id": pid, "payload": place, "retrieved_at": at})

    queue: list[Tile] = []
    try:
        for idx, (c, ptype) in enumerate(work):
            if c != cat:
                continue
            queue = list(roots)
            try:
                while queue:
                    tile = queue[0]
                    try:
                        res = search_tile(client, ptype, tile)
                    except CapWithPartial as e:
                        add(tile, e.places)  # land pages fetched before the cap
                        raise
                    queue.pop(0)
                    add(tile, res.places)
                    if res.error:
                        status, msg, rh = res.error
                        errors.append({"category": cat, "tile_id": tile.tile_id,
                                       "request_hash": rh, "http_status": status, "error": msg})
                        log("places_error", category=cat, type=ptype, tile_id=tile.tile_id,
                            http_status=status)
                    if res.saturated:
                        saturated += 1
                        log("tile_saturated", category=cat, type=ptype, tile_id=tile.tile_id,
                            size_km=tile.size_km)
                        if tile.size_km / 2 >= min_km:
                            queue.extend(split(tile))
                        else:
                            gaps += 1
                            log("coverage_gap", category=cat, type=ptype, tile_id=tile.tile_id,
                                size_km=tile.size_km)
            except CapReached:
                capped_at = idx
                break
    finally:  # land rows already fetched even on AuthError / unexpected errors
        stats.items_ok += insert_rows(con, "places", rows, run)
        stats.items_failed += insert_rows(con, "places_errors", errors, run)
        stats.notes += [f"{cat}:saturated_tiles={saturated}", f"{cat}:coverage_gaps={gaps}"]
    if capped_at is not None:
        later = [w for w in work[capped_at + 1:] if w[0] == cat]
        stats.notes.append(f"{cat}:unqueried_tiles={len(queue) + len(roots) * len(later)}")
        stats.status = "capped"


def note_int(notes: str | None, category: str, key: str) -> int:
    """Value of the `{category}:{key}=N` entry in run_log.notes (0 when absent)."""
    m = re.search(rf"(?:^|; ){re.escape(category)}:{re.escape(key)}=(\d+)", notes or "")
    return int(m.group(1)) if m else 0


_note_int = note_int  # backwards-compatible alias


def places_summary(con: duckdb.DuckDBPyConnection, run_id: str) -> list[dict]:
    row = con.execute(
        "select notes from raw.run_log where run_id = ? and step = 'places'"
        " order by finished_at desc limit 1", [run_id]).fetchone()
    notes = row[0] if row else None
    counts = {r[0]: r[1:] for r in con.execute(
        "select category, count(*), count(distinct place_id),"
        " count(*) filter (where payload->>'websiteUri' is not null)"
        " from raw.places where run_id = ? group by 1", [run_id]).fetchall()}
    cats = sorted(set(counts) | set(re.findall(r"(?:^|; )([^:;]+):saturated_tiles=", notes or "")))
    out = []
    for c in cats:
        n, uniq, web = counts.get(c, (0, 0, 0))
        out.append({"category": c, "places": n, "unique_places": uniq, "with_website": web,
                    "saturated_tiles": note_int(notes, c, "saturated_tiles"),
                    "coverage_gaps": note_int(notes, c, "coverage_gaps")})
    return out


def main(argv: list[str] | None = None, *, client: SourceClient | None = None,
         con: duckdb.DuckDBPyConnection | None = None,
         config: AppConfig | None = None) -> int:
    """CLI entry. client/con/config are test seams; by default built from settings."""
    ap = argparse.ArgumentParser(prog="python -m src.extract.places")
    ap.add_argument("--category", action="append", help="category to run (repeatable)")
    ap.add_argument("--run-id", help="reuse an existing run id")
    args = ap.parse_args(argv)

    settings = load_settings_or_exit() if client is None or con is None or config is None else None
    if config is None:
        config = load_config(settings.config_path)
    unknown = sorted(set(args.category or []) - set(config.categories))
    if unknown:
        ap.error(f"unknown category: {', '.join(unknown)} (known: {', '.join(sorted(config.categories))})")
    if args.run_id:
        month = args.run_id.split("_")[0]
        run = RunContext(args.run_id, month, new_run(month).started_at)
    else:
        run = new_run(settings.run_month if settings else None)
    own_con = con is None
    if con is None:
        con = connect(settings.db_path)
    try:
        if client is None:
            try:
                client = places_client(settings)
            except MissingSetting as e:
                print(f"missing setting: {e.name}", file=sys.stderr)
                return 2
        try:
            stats = run_places(con, client, config, run, args.category)
        except Exception as exc:
            log("error", step="places", run_id=run.run_id, error=type(exc).__name__)
            return 1
        for row in places_summary(con, run.run_id):
            log("places_summary", run_id=run.run_id, **row)
        if stats.status == "capped":
            log("warning", step="places", run_id=run.run_id, message="request cap reached")
        elif stats.items_failed:
            log("warning", step="places", run_id=run.run_id,
                message=f"partial: {stats.items_failed} failed items")
        return 0
    finally:
        if own_con:
            con.close()


if __name__ == "__main__":
    sys.exit(main())
