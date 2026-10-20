import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from src.cache import (
    ApiError,
    AuthError,
    CapReached,
    DiskCache,
    SourceClient,
    TokenBucket,
    request_hash,
    robots_client,
    sodra_client,
    places_client,
    firecrawl_client,
)
from src.settings import Settings

URL = "https://api.example.test/v1/search"
KEY = "SECRET-KEY-123456"


def make_client(tmp_path, responses, *, max_requests=None, ttl=timedelta(days=30), cache=True):
    """Client over a MockTransport serving `responses` in order (last repeats)."""
    calls = []
    seq = list(responses)

    def handler(request):
        calls.append(request)
        r = seq.pop(0) if len(seq) > 1 else seq[0]
        return r() if callable(r) else r

    sleeps = []
    client = SourceClient(
        "test",
        DiskCache(tmp_path / "cache", ttl) if cache else None,
        TokenBucket(1000, sleep=lambda s: None),
        max_requests,
        {"X-Goog-Api-Key": KEY},
        transport=httpx.MockTransport(handler),
        sleep=sleeps.append,
    )
    return client, calls, sleeps


def ok(data=None):
    return httpx.Response(200, json=data or {"ok": True})


def test_request_hash_is_stable():
    # Recorded fixture filenames depend on this exact value.
    assert (
        request_hash("post", "https://x.test/a", {"b": 1, "a": [2]})
        == "afb8a6e50b104e6a40ccc9e5bd14e211bf6346b13e65a2c008e8962e5caef4cb"
    )


def test_second_identical_request_hits_cache(tmp_path):
    c, calls, _ = make_client(tmp_path, [ok()])
    a = c.request("POST", URL, {"q": 1})
    b = c.request("POST", URL, {"q": 1})
    assert len(calls) == 1
    assert (c.api_requests, c.cache_hits) == (1, 1)
    assert not a.from_cache and b.from_cache
    assert b.json() == {"ok": True}


def test_different_body_misses_cache(tmp_path):
    c, calls, _ = make_client(tmp_path, [ok()])
    c.request("POST", URL, {"q": 1})
    c.request("POST", URL, {"q": 2})
    assert len(calls) == 2 and c.cache_hits == 0


def test_use_cache_false_refetches_and_overwrites(tmp_path):
    c, calls, _ = make_client(tmp_path, [ok({"v": 1}), ok({"v": 2})])
    c.request("GET", URL)
    r = c.request("GET", URL, use_cache=False)
    assert len(calls) == 2 and r.json() == {"v": 2}
    assert c.request("GET", URL).json() == {"v": 2}


def test_api_key_not_in_hash_or_cache_files(tmp_path):
    c, _, _ = make_client(tmp_path, [ok()])
    c.request("POST", URL, {"q": 1})
    files = [p for p in (tmp_path / "cache").rglob("*") if p.is_file()]
    assert files
    for p in files:
        assert KEY not in p.name and KEY.encode() not in p.read_bytes()
    assert KEY not in request_hash("POST", URL, {"q": 1})


def test_cache_file_layout_and_non_2xx_not_stored(tmp_path):
    cache = DiskCache(tmp_path, None)
    key = request_hash("GET", URL, None)
    cache.put(key, 404, {}, b"no", datetime.now(UTC))
    assert cache.get(key) is None
    cache.put(key, 200, {"content-type": "x"}, b"\x00\xff", datetime.now(UTC))
    assert (tmp_path / key[:2] / f"{key}.json").exists()
    got = cache.get(key)
    assert got.content == b"\x00\xff" and got.from_cache and got.headers == {"content-type": "x"}


def test_ttl_expiry(tmp_path):
    cache = DiskCache(tmp_path, timedelta(days=30))
    cache.put("ab" + "0" * 62, 200, {}, b"{}", datetime.now(UTC) - timedelta(days=31))
    cache.put("cd" + "0" * 62, 200, {}, b"{}", datetime.now(UTC) - timedelta(days=29))
    assert cache.get("ab" + "0" * 62) is None
    assert cache.get("cd" + "0" * 62) is not None


def test_no_ttl_never_expires(tmp_path):
    cache = DiskCache(tmp_path, None)
    cache.put("ab" + "0" * 62, 200, {}, b"{}", datetime.now(UTC) - timedelta(days=9999))
    assert cache.get("ab" + "0" * 62) is not None


def test_retries_429_then_success(tmp_path):
    c, calls, sleeps = make_client(tmp_path, [httpx.Response(429), ok()])
    assert c.request("GET", URL).status == 200
    assert len(calls) == 2 and c.api_requests == 2 and len(sleeps) == 1


def test_retry_after_honoured(tmp_path):
    c, _, sleeps = make_client(tmp_path, [httpx.Response(429, headers={"Retry-After": "7"}), ok()])
    c.request("GET", URL)
    assert sleeps == [7]


def test_retry_after_over_60_ignored(tmp_path):
    c, _, sleeps = make_client(tmp_path, [httpx.Response(429, headers={"Retry-After": "600"}), ok()])
    c.request("GET", URL)
    assert sleeps and sleeps[0] <= 20


def test_5xx_exhausts_retries(tmp_path):
    c, calls, _ = make_client(tmp_path, [httpx.Response(503, text="down")])
    with pytest.raises(ApiError) as e:
        c.request("GET", URL)
    assert e.value.status == 503 and len(calls) == 3 and c.api_requests == 3


def test_transport_error_retried(tmp_path):
    def boom():
        raise httpx.ConnectTimeout("t")

    c, calls, _ = make_client(tmp_path, [boom, ok()])
    assert c.request("GET", URL).status == 200 and len(calls) == 2


def test_transport_error_exhausted_is_api_error(tmp_path):
    def boom():
        raise httpx.ConnectError("c")

    c, calls, _ = make_client(tmp_path, [boom])
    with pytest.raises(ApiError) as e:
        c.request("GET", URL)
    assert e.value.status is None and len(calls) == 3


def test_400_not_retried(tmp_path):
    c, calls, _ = make_client(tmp_path, [httpx.Response(400, text="x" * 1000)])
    with pytest.raises(ApiError) as e:
        c.request("GET", URL)
    assert e.value.status == 400 and len(calls) == 1
    assert len(e.value.message) <= 400 and KEY not in e.value.message


@pytest.mark.parametrize("status", [401, 403])
def test_auth_error(tmp_path, status):
    c, calls, _ = make_client(tmp_path, [httpx.Response(status)])
    with pytest.raises(AuthError):
        c.request("GET", URL)
    assert len(calls) == 1


def test_non_2xx_not_cached(tmp_path):
    c, calls, _ = make_client(tmp_path, [httpx.Response(400), ok()])
    with pytest.raises(ApiError):
        c.request("GET", URL)
    assert c.request("GET", URL).status == 200 and len(calls) == 2


def test_cap_raises_before_send(tmp_path):
    c, calls, _ = make_client(tmp_path, [ok()], max_requests=1)
    c.request("POST", URL, {"q": 1})
    with pytest.raises(CapReached):
        c.request("POST", URL, {"q": 2})
    assert len(calls) == 1
    c.request("POST", URL, {"q": 1})  # cache hit does not touch the cap
    assert c.cache_hits == 1


def test_cap_counts_retries(tmp_path):
    c, calls, _ = make_client(tmp_path, [httpx.Response(500)], max_requests=2)
    with pytest.raises(CapReached):
        c.request("GET", URL)
    assert len(calls) == 2


def test_no_cache_client(tmp_path):
    c, calls, _ = make_client(tmp_path, [ok()], cache=False)
    c.request("GET", URL)
    c.request("GET", URL)
    assert len(calls) == 2


def test_token_bucket_spacing():
    now = [0.0]
    slept = []

    def sleep(s):
        slept.append(s)
        now[0] += s

    b = TokenBucket(5, clock=lambda: now[0], sleep=sleep)
    for _ in range(3):
        b.acquire()
    assert sum(slept) == pytest.approx(0.4)


def test_factories(tmp_path):
    s = Settings(google_api_key="g-key", firecrawl_api_key="f-key", cache_dir=tmp_path,
                 config_path="config/categories.yaml", _env_file=None)
    p, f, r, so = (fn(s) for fn in (places_client, firecrawl_client, robots_client, sodra_client))
    assert p.headers["X-Goog-Api-Key"] == "g-key" and p.max_requests == 1000
    assert f.headers["Authorization"] == "Bearer f-key" and f.max_requests is None
    assert r.headers["User-Agent"] and r.max_requests is None
    assert so.cache is None and so.headers["User-Agent"] == r.headers["User-Agent"]
    assert p.cache.ttl == timedelta(days=30) and r.cache.ttl == timedelta(days=1)
    assert f.limiter.interval == pytest.approx(6.0)


def test_follow_redirects_opt_in(tmp_path):
    def handler(request):
        if request.url.path == "/start":
            return httpx.Response(302, headers={"Location": "https://api.example.test/final"})
        return httpx.Response(200, content=b"done")

    def build(follow):
        return SourceClient("t", None, TokenBucket(1000, sleep=lambda s: None), None, {},
                            transport=httpx.MockTransport(handler), follow_redirects=follow)

    r = build(True).request("GET", "https://api.example.test/start")
    assert (r.status, r.content) == (200, b"done")
    with pytest.raises(ApiError):
        build(False).request("GET", "https://api.example.test/start")


def test_auth_error_carries_headers_and_body(tmp_path):
    client, _, _ = make_client(tmp_path, [httpx.Response(403, headers={"cf-mitigated": "challenge"}, text="Just a moment...")])
    with pytest.raises(AuthError) as ei:
        client.request("GET", URL)
    assert ei.value.headers["cf-mitigated"] == "challenge"
    assert "Just a moment" in ei.value.body


def test_per_request_headers_merged_and_not_hashed(tmp_path):
    client, calls, _ = make_client(tmp_path, [ok()])
    client.request("POST", URL, {"a": 1}, headers={"X-Goog-FieldMask": "places.id"})
    assert calls[0].headers["X-Goog-FieldMask"] == "places.id"
    assert calls[0].headers["X-Goog-Api-Key"] == KEY
    # same body, different headers -> cache hit
    r = client.request("POST", URL, {"a": 1}, headers={"X-Goog-FieldMask": "other"})
    assert r.from_cache and len(calls) == 1
