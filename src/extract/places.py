"""Places API (Text Search New) -> raw.places via a per-type quadtree of tiles."""
from dataclasses import dataclass, field
from datetime import datetime

import duckdb

from src.cache import ApiError, CapReached, SourceClient, request_hash
from src.db import StepStats, insert_rows, step
from src.extract.tiles import Tile, root_tiles, split
from src.log import log
from src.run import RunContext
from src.settings import AppConfig

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
    def __init__(self, page: int, error: ApiError, req_hash: str):
        super().__init__(str(error))
        self.page, self.error, self.req_hash = page, error, req_hash


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
            raise _PageFailed(page, e, rh) from None
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
        return TileResult(error=(e.error.status, e.error.message, e.req_hash))
    return TileResult(places=places, saturated=len(places) >= MAX_RESULTS)


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
    min_km = config.area.min_tile_km
    for cat in names:
        seen: set[str] = set()
        rows: list[dict] = []
        errors: list[dict] = []
        saturated = gaps = 0
        capped_at: int | None = None
        queue: list[Tile] = []
        for idx, (c, ptype) in enumerate(work):
            if c != cat:
                continue
            queue = list(roots)
            try:
                while queue:
                    tile = queue[0]
                    res = search_tile(client, ptype, tile)
                    queue.pop(0)
                    for pid, place, rh, at in res.places:
                        if pid in seen:
                            continue
                        seen.add(pid)
                        rows.append({"category": cat, "tile_id": tile.tile_id, "request_hash": rh,
                                     "place_id": pid, "payload": place, "retrieved_at": at})
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
        stats.items_ok += insert_rows(con, "places", rows, run)
        stats.items_failed += insert_rows(con, "places_errors", errors, run)
        stats.notes += [f"{cat}:saturated_tiles={saturated}", f"{cat}:coverage_gaps={gaps}"]
        if capped_at is not None:
            remaining = len(queue) + len(roots) * len(work[capped_at + 1:])
            stats.notes.append(f"unqueried_tiles={remaining}")
            stats.status = "capped"
            return
