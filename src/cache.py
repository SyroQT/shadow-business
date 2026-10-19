"""On-disk HTTP cache, rate limiting, retries and request counters."""
import base64
import hashlib
import json
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
from tenacity import (
    Retrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)

from src.settings import Settings, load_config

PLACES_TTL = timedelta(days=30)
FIRECRAWL_TTL = timedelta(days=30)
ROBOTS_TTL = timedelta(days=1)
MAX_ATTEMPTS = 3
MAX_RETRY_AFTER = 60
MESSAGE_LIMIT = 300


class CapReached(Exception):
    """The per-source request budget is exhausted."""


class AuthError(Exception):
    """401/403 from the API. Response headers and a body snippet are kept for diagnosis."""

    def __init__(self, message: str, headers: dict | None = None, body: str = ""):
        super().__init__(message)
        self.headers = headers or {}
        self.body = body


class ApiError(Exception):
    def __init__(self, status: int | None, message: str):
        super().__init__(f"{status}: {message}")
        self.status = status
        self.message = message


def request_hash(method: str, url: str, body: dict | None) -> str:
    """Cache key. Headers are never part of it, so API keys stay out."""
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(f"{method.upper()}\n{url}\n{canonical}".encode()).hexdigest()


@dataclass
class CachedResponse:
    status: int
    headers: dict
    content: bytes
    fetched_at: datetime
    from_cache: bool = False

    def json(self):
        return json.loads(self.content)

    @property
    def text(self) -> str:
        return self.content.decode("utf-8", errors="replace")


class DiskCache:
    def __init__(self, root: Path, ttl: timedelta | None):
        self.root = Path(root)
        self.ttl = ttl

    def _path(self, key: str) -> Path:
        return self.root / key[:2] / f"{key}.json"

    def get(self, key: str) -> CachedResponse | None:
        path = self._path(key)
        try:
            rec = json.loads(path.read_text())
            fetched_at = datetime.fromisoformat(rec["fetched_at"])
            content = base64.b64decode(rec["body"])
            status, headers = rec["status"], rec["headers"]
        except (OSError, ValueError, KeyError):
            return None
        if self.ttl is not None and datetime.now(UTC) - fetched_at > self.ttl:
            return None
        return CachedResponse(status, headers, content, fetched_at, True)

    def put(self, key: str, status: int, headers: dict, body: bytes, fetched_at: datetime) -> None:
        if not 200 <= status < 300:
            return
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        rec = {
            "status": status,
            "headers": dict(headers),
            "fetched_at": fetched_at.isoformat(),
            "body": base64.b64encode(body).decode("ascii"),
        }
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(rec))
        tmp.replace(path)


class TokenBucket:
    def __init__(self, rate_per_sec: float, burst: int = 1, clock=time.monotonic, sleep=time.sleep):
        self.rate = rate_per_sec
        self.interval = 1.0 / rate_per_sec
        self.burst = burst
        self.clock = clock
        self.sleep = sleep
        self.tokens = float(burst)
        self.last = clock()

    def acquire(self) -> None:
        now = self.clock()
        self.tokens = min(self.burst, self.tokens + (now - self.last) * self.rate)
        self.last = now
        if self.tokens < 1:
            wait = (1 - self.tokens) * self.interval
            self.sleep(wait)
            self.last = self.clock()
            self.tokens = 1.0
        self.tokens -= 1


class _Retryable(Exception):
    def __init__(self, status: int, message: str, retry_after: float | None = None):
        super().__init__(message)
        self.status = status
        self.message = message
        self.retry_after = retry_after


def _short(text: str) -> str:
    return text[:MESSAGE_LIMIT]


def _retry_after(resp: httpx.Response) -> float | None:
    try:
        value = float(resp.headers.get("Retry-After", ""))
    except ValueError:
        return None
    return value if 0 <= value <= MAX_RETRY_AFTER else None


class SourceClient:
    def __init__(
        self,
        source: str,
        cache: DiskCache | None,
        limiter: TokenBucket,
        max_requests: int | None,
        headers: dict,
        transport: httpx.BaseTransport | None = None,
        timeout: float = 30,
        sleep=time.sleep,
        follow_redirects: bool = False,
    ):
        self.follow_redirects = follow_redirects
        self.source = source
        self.cache = cache
        self.limiter = limiter
        self.max_requests = max_requests
        self.headers = headers
        self.transport = transport
        self.timeout = timeout
        self.sleep = sleep
        self.api_requests = 0
        self.cache_hits = 0

    def _wait(self, state) -> float:
        exc = state.outcome.exception() if state.outcome else None
        if isinstance(exc, _Retryable) and exc.retry_after is not None:
            return exc.retry_after
        return wait_exponential_jitter(initial=1, max=20)(state)

    def request(self, method: str, url: str, json_body: dict | None = None,
                use_cache: bool = True) -> CachedResponse:
        key = request_hash(method, url, json_body)
        if use_cache and self.cache is not None:
            hit = self.cache.get(key)
            if hit is not None:
                self.cache_hits += 1
                return hit

        retrying = Retrying(
            stop=stop_after_attempt(MAX_ATTEMPTS),
            wait=self._wait,
            retry=retry_if_exception_type((httpx.TimeoutException, httpx.TransportError, _Retryable)),
            sleep=self.sleep,
            reraise=True,
        )
        with httpx.Client(transport=self.transport, timeout=self.timeout, headers=self.headers,
                          follow_redirects=self.follow_redirects) as http:
            try:
                resp = retrying(self._attempt, http, method, url, json_body)
            except _Retryable as e:
                raise ApiError(e.status, e.message) from None
            except httpx.HTTPError as e:
                raise ApiError(None, _short(f"{type(e).__name__}: {e}")) from None

        fetched_at = datetime.now(UTC)
        headers = dict(resp.headers)
        if self.cache is not None:
            self.cache.put(key, resp.status_code, headers, resp.content, fetched_at)
        return CachedResponse(resp.status_code, headers, resp.content, fetched_at, False)

    def _attempt(self, http: httpx.Client, method, url, json_body) -> httpx.Response:
        if self.max_requests is not None and self.api_requests >= self.max_requests:
            raise CapReached(f"{self.source}: {self.max_requests} requests reached")
        self.limiter.acquire()
        self.api_requests += 1
        resp = http.request(method, url, json=json_body)
        status = resp.status_code
        if 200 <= status < 300:
            return resp
        if status in (401, 403):
            raise AuthError(f"{self.source}: HTTP {status}", dict(resp.headers), _short(resp.text))
        if status == 429 or status >= 500:
            raise _Retryable(status, _short(resp.text), _retry_after(resp) if status == 429 else None)
        raise ApiError(status, _short(resp.text))


def _cache(settings: Settings, source: str, ttl: timedelta | None) -> DiskCache:
    return DiskCache(settings.cache_dir / source, ttl)


def places_client(settings: Settings, **kw) -> SourceClient:
    return SourceClient(
        "places", _cache(settings, "places", PLACES_TTL),
        TokenBucket(settings.places_rate_per_sec),
        settings.max_places_requests,
        {"X-Goog-Api-Key": settings.google_api_key.get_secret_value()}, **kw)


def firecrawl_client(settings: Settings, **kw) -> SourceClient:
    return SourceClient(
        "firecrawl", _cache(settings, "firecrawl", FIRECRAWL_TTL),
        TokenBucket(settings.firecrawl_rate_per_min / 60),
        None,
        {"Authorization": f"Bearer {settings.firecrawl_api_key.get_secret_value()}"}, **kw)


def _user_agent(settings: Settings) -> str:
    return load_config(settings.config_path).crawl.user_agent


def robots_client(settings: Settings, **kw) -> SourceClient:
    return SourceClient(
        "robots", _cache(settings, "robots", ROBOTS_TTL),
        TokenBucket(settings.places_rate_per_sec), None,
        {"User-Agent": _user_agent(settings)}, follow_redirects=True, **kw)


def sodra_client(settings: Settings, **kw) -> SourceClient:
    return SourceClient(
        "sodra", None, TokenBucket(1), None,
        {"User-Agent": _user_agent(settings)}, follow_redirects=True, **kw)
