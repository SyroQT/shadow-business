import json
from pathlib import Path

import httpx
import pytest

from src.cache import AuthError, DiskCache, SourceClient, TokenBucket, request_hash
from src.extract.places import (
    FIELD_MASK,
    SEARCH_URL,
    build_body,
    run_places,
    search_tile,
)
from src.extract.tiles import Tile
from src.run import new_run
from src.settings import AppConfig
from tests.replay import replay_transport

FIX = Path(__file__).parent / "fixtures" / "places"
RUN = new_run("2026-10")
TILE = Tile("demo_oldtown_1km", 54.679, 25.276, 54.688, 25.291, 1.0)
BBOX = {"south": 54.679, "west": 25.276, "north": 54.688, "east": 25.291}


def fx(name: str) -> dict:
    return json.loads((FIX / f"{name}.json").read_text())


FULL = {None: "page_full_1", "fake_token_1": "page_full_2", "fake_token_2": "page_full_3"}


def full_page(body: dict) -> httpx.Response:
    return httpx.Response(200, json=fx(FULL[body.get("pageToken")]))


def partial_page(body: dict) -> httpx.Response:
    return httpx.Response(200, json=fx("page_partial"))


def make_client(tmp_path, handler, max_requests=None):
    calls = []

    def transport_handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        calls.append((body, request))
        return handler(body)

    client = SourceClient(
        "places", DiskCache(tmp_path / "cache", None), TokenBucket(1000, sleep=lambda s: None),
        max_requests, {"X-Goog-Api-Key": "k"}, transport=httpx.MockTransport(transport_handler),
        sleep=lambda s: None)
    return client, calls


def config(categories: dict[str, list[str]], tile_km=1.0, min_tile_km=0.5) -> AppConfig:
    return AppConfig.model_validate({
        "area": {"name": "t", "bbox": BBOX, "tile_km": tile_km, "min_tile_km": min_tile_km},
        "categories": {c: {"places_included_type": t, "evrk_prefixes": []}
                       for c, t in categories.items()},
        "crawl": {"user_agent": "x", "social_domains": [], "legal_link_pattern": "x"},
    })


def mid_lat() -> float:
    return (BBOX["south"] + BBOX["north"]) / 2


def is_child(body: dict, n: int) -> bool:
    """Is the request for quadrant n of the root tile (n=0 SW .. 3 NE)?"""
    r = body["locationRestriction"]["rectangle"]
    south = n < 2
    west = n % 2 == 0
    return (r["low"]["latitude"] == (BBOX["south"] if south else mid_lat())
            and r["low"]["longitude"] == (BBOX["west"] if west else
                                          (BBOX["west"] + BBOX["east"]) / 2))


def is_root(body: dict) -> bool:
    r = body["locationRestriction"]["rectangle"]
    return (r["low"]["latitude"], r["low"]["longitude"]) == (BBOX["south"], BBOX["west"]) \
        and (r["high"]["latitude"], r["high"]["longitude"]) == (BBOX["north"], BBOX["east"])


def split_handler(body):
    """Root and its SW child are saturated; other children are partial."""
    if is_root(body) or is_child(body, 0):
        return full_page(body)
    return partial_page(body)


def rows(con, table="places"):
    return con.execute(f"select * from raw.{table}").fetchall()


def log_row(con):
    return con.execute(
        "select status, items_ok, items_failed, api_requests, cache_hits, notes from raw.run_log"
    ).fetchone()


def test_build_body_and_field_mask_header(tmp_path):
    t = Tile("t", 54.0, 25.0, 54.1, 25.1, 1.0)
    b = build_body("hair_salon", t, None)
    assert b["textQuery"] == "hair salon" and b["includedType"] == "hair_salon"
    assert b["strictTypeFiltering"] is True and b["pageSize"] == 20
    assert "pageToken" not in b
    assert b["locationRestriction"]["rectangle"] == {
        "low": {"latitude": 54.0, "longitude": 25.0}, "high": {"latitude": 54.1, "longitude": 25.1}}
    assert build_body("hair_salon", t, "abc")["pageToken"] == "abc"
    client, calls = make_client(tmp_path, partial_page)
    search_tile(client, "cafe", t)
    assert calls[0][1].headers["X-Goog-FieldMask"] == FIELD_MASK
    assert FIELD_MASK.endswith("nextPageToken") and FIELD_MASK.startswith("places.id,")


def test_pagination_collects_three_pages(tmp_path):
    client, calls = make_client(tmp_path, full_page)
    r = search_tile(client, "cafe", TILE)
    assert len(r.places) == 60 and r.saturated and r.error is None
    assert len(calls) == 3
    assert [c[0].get("pageToken") for c in calls] == [None, "fake_token_1", "fake_token_2"]
    pid, place, rh, at = r.places[0]
    assert pid == place["id"] == "ChIJ_fake_001"
    assert rh == request_hash("POST", SEARCH_URL, calls[0][0])
    assert at.tzinfo is not None


def test_partial_page_not_saturated(tmp_path):
    client, calls = make_client(tmp_path, partial_page)
    r = search_tile(client, "cafe", TILE)
    assert len(r.places) == 7 and not r.saturated and len(calls) == 1


def test_stale_page_token_refetches_chain(tmp_path):
    seen = {"p2": 0}

    def handler(body):
        if body.get("pageToken") == "fake_token_1":
            seen["p2"] += 1
            if seen["p2"] == 1:
                return httpx.Response(400, json={"error": {"message": "stale token"}})
        return full_page(body)

    client, calls = make_client(tmp_path, handler)
    r = search_tile(client, "cafe", TILE)
    assert len(r.places) == 60 and r.error is None
    assert len(calls) == 5  # p1, p2(400), then p1, p2, p3 bypassing the cache


def test_stale_token_twice_gives_error(tmp_path):
    def handler(body):
        if body.get("pageToken"):
            return httpx.Response(400, json={"error": {"message": "bad token"}})
        return full_page(body)

    client, calls = make_client(tmp_path, handler)
    r = search_tile(client, "cafe", TILE)
    assert r.error is not None and r.error[0] == 400 and not r.saturated
    assert len(calls) == 4


def test_non_400_error_on_later_page_not_refetched(tmp_path):
    def handler(body):
        if body.get("pageToken"):
            return httpx.Response(404, json={})
        return full_page(body)

    client, calls = make_client(tmp_path, handler)
    r = search_tile(client, "cafe", TILE)
    assert r.error[0] == 404 and len(calls) == 2


def test_auth_error_propagates_from_search_tile(tmp_path):
    client, _ = make_client(tmp_path, lambda b: httpx.Response(403, json={}))
    with pytest.raises(AuthError):
        search_tile(client, "cafe", TILE)


def test_saturation_splits_until_min(con, tmp_path):
    client, calls = make_client(tmp_path, split_handler)
    stats = run_places(con, client, config({"cat": ["cafe"]}), RUN)
    # root (3 pages) + SW child (3 pages) + 3 partial children
    assert len(calls) == 9
    assert "cat:saturated_tiles=2" in stats.notes
    assert "cat:coverage_gaps=1" in stats.notes
    # the 0.5 km tile is not split: only 5 distinct tiles queried
    assert len({json.dumps(c[0]["locationRestriction"], sort_keys=True) for c in calls}) == 5
    assert stats.status is None and stats.items_ok == 60 + 7


def test_dedupe_within_category_not_across(con, tmp_path):
    client, _ = make_client(tmp_path, partial_page)
    cfg = config({"a": ["cafe", "coffee_shop"], "b": ["cafe"]}, min_tile_km=1.0)
    stats = run_places(con, client, cfg, RUN)
    assert stats.items_ok == 14
    per = dict(con.execute("select category, count(*) from raw.places group by 1").fetchall())
    assert per == {"a": 7, "b": 7}
    assert "a:saturated_tiles=0" in stats.notes and "b:coverage_gaps=0" in stats.notes


def test_error_row_and_continue(con, tmp_path):
    def handler(body):
        if is_child(body, 1):
            return httpx.Response(500, text="boom")
        return split_handler(body)

    client, _ = make_client(tmp_path, handler)
    stats = run_places(con, client, config({"cat": ["cafe"]}), RUN)
    errs = con.execute(
        "select category, tile_id, http_status, request_hash from raw.places_errors").fetchall()
    assert len(errs) == 1 and errs[0][:3] == ("cat", "r0c0.1", 500) and len(errs[0][3]) == 64
    assert stats.items_failed == 1 and stats.items_ok == 60 + 7
    assert log_row(con)[0] == "partial"


def test_auth_error_fails_step(con, tmp_path):
    client, calls = make_client(tmp_path, lambda b: httpx.Response(403, json={}))
    with pytest.raises(AuthError):
        run_places(con, client, config({"cat": ["cafe"]}), RUN)
    assert len(calls) == 1
    assert log_row(con)[0] == "failed"
    assert rows(con) == []


def test_cap_mid_tree_stops_cleanly(con, tmp_path):
    client, calls = make_client(tmp_path, split_handler, max_requests=5)
    stats = run_places(con, client, config({"cat": ["cafe"]}), RUN)
    status, items_ok, _, api, _, notes = log_row(con)
    assert status == "capped" and api == 5 == len(calls)
    assert items_ok == 60 and len(rows(con)) == 60
    assert "unqueried_tiles=" in notes and "cat:saturated_tiles=" in notes
    assert stats.status == "capped"


def test_cap_counts_remaining_roots(con, tmp_path):
    client, _ = make_client(tmp_path, partial_page, max_requests=1)
    cfg = config({"a": ["cafe", "bar"], "b": ["cafe"]}, min_tile_km=1.0)
    stats = run_places(con, client, cfg, RUN)
    # 1 request lands tile 1 of a/cafe; a/bar and b/cafe (1 root each) remain
    # (cap hits when a/bar is first requested, so it counts as unqueried too)
    assert "unqueried_tiles=2" in stats.notes
    assert stats.items_ok == 7


def test_rows_carry_page_retrieved_at_and_hash(con, tmp_path):
    client, calls = make_client(tmp_path, partial_page)
    run_places(con, client, config({"cat": ["cafe"]}, min_tile_km=1.0), RUN)
    rh, tile_id, ts, payload = con.execute(
        "select request_hash, tile_id, retrieved_at, payload->>'id' from raw.places "
        "order by place_id limit 1").fetchone()
    assert rh == request_hash("POST", SEARCH_URL, calls[0][0])
    assert tile_id == "r0c0" and payload == "ChIJ_fake_101"
    assert ts.tzinfo is None  # naive UTC


def test_run_log_counts(con, tmp_path):
    client, calls = make_client(tmp_path, split_handler)
    run_places(con, client, config({"cat": ["cafe"]}), RUN)
    assert log_row(con)[3] == len(calls) == client.api_requests
    # second run served from cache: no new transport calls
    client2, calls2 = make_client(tmp_path, split_handler)
    run_places(con, client2, config({"cat": ["cafe"]}), RUN)
    assert calls2 == []
    assert con.execute("select cache_hits from raw.run_log order by started_at desc").fetchone()[0] == 9


def test_categories_filter(con, tmp_path):
    client, _ = make_client(tmp_path, partial_page)
    run_places(con, client, config({"a": ["cafe"], "b": ["cafe"]}, min_tile_km=1.0), RUN,
               categories=["b"])
    assert {r[0] for r in con.execute("select category from raw.places").fetchall()} == {"b"}


@pytest.mark.recorded
def test_recorded_replay_search_and_run(con, tmp_path):
    calls: list = []
    transport = replay_transport("places", calls=calls)

    def client():
        return SourceClient(
            "places", None, TokenBucket(1000, sleep=lambda s: None), None,
            {"X-Goog-Api-Key": "k"}, transport=transport, sleep=lambda s: None)

    cafe = search_tile(client(), "cafe", TILE)
    assert len(cafe.places) == 45 and not cafe.saturated
    resto = search_tile(client(), "restaurant", TILE)
    assert len(resto.places) == 60 and resto.saturated
    assert len(calls) == 6

    calls.clear()
    cfg = config({"cafes_restaurants": ["cafe", "restaurant"]}, tile_km=1.0, min_tile_km=1.0)
    c = client()
    stats = run_places(con, c, cfg, RUN)
    assert len(calls) == 6 == c.api_requests
    assert con.execute("select count(*) from raw.places").fetchone()[0] == stats.items_ok > 0
    status, _, _, api, _, notes = log_row(con)
    assert api == 6 and status == "ok"
    assert "cafes_restaurants:saturated_tiles=1" in notes
    assert "cafes_restaurants:coverage_gaps=1" in notes


@pytest.mark.live
def test_live_one_tile_one_type(tmp_path):
    from src.cache import places_client
    from src.settings import load_settings

    s = load_settings()
    s = s.model_copy(update={"max_places_requests": 3, "cache_dir": tmp_path})
    tile = Tile("live_vilnius_1km", 54.6790, 25.2760, 54.6880, 25.2910, 1.0)
    r = search_tile(places_client(s), "cafe", tile)
    assert len(r.places) >= 1
