"""Firecrawl over raw_places URLs -> raw_web_pages."""
import hashlib
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from src.cache import ApiError, AuthError, SourceClient
from src.db import insert_rows, step
from src.extract.robots import RobotsChecker
from src.settings import AppConfig, CrawlConfig

__all__ = ["RobotsChecker"]

FIRECRAWL_URL = "https://api.firecrawl.dev/v2/scrape"

_DROP_KEYS = {"fbclid", "gclid", "yclid", "mc_cid", "mc_eid", "ref", "_ga"}
_DEFAULT_PORTS = {"http": 80, "https": 443}
_OTHER_SCHEME = re.compile(r"^[a-z][a-z0-9+.-]*:(?!\d)", re.I)

Cls = Literal["no_website", "social_domain", "crawl"]


def _split(url: str):
    url = url.strip()
    if "://" not in url and not url.startswith("//") and not _OTHER_SCHEME.match(url):
        url = "https://" + url
    elif url.startswith("//"):
        url = "https:" + url
    parts = urlsplit(url)
    if parts.scheme.lower() not in ("http", "https"):
        raise ValueError(f"unsupported scheme in {url!r}")
    try:
        parts.port
    except ValueError as e:
        raise ValueError(f"bad port in {url!r}") from e
    host = (parts.hostname or "").rstrip(".").lower()
    if not host or re.search(r"\s", host):
        raise ValueError(f"no host in {url!r}")
    return parts, host


def normalise_url(url: str) -> str:
    parts, host = _split(url)
    scheme = parts.scheme.lower()
    try:
        port = parts.port
    except ValueError as e:
        raise ValueError(f"bad port in {url!r}") from e
    netloc = f"[{host}]" if ":" in host else host
    if port is not None and port != _DEFAULT_PORTS[scheme]:
        netloc += f":{port}"
    query = sorted(
        (k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if not k.startswith("utm_") and k not in _DROP_KEYS
    )
    return urlunsplit((scheme, netloc, parts.path or "/", urlencode(query), ""))


def dedupe_key(norm_url: str) -> str:
    parts = urlsplit(norm_url)
    host = parts.netloc.lower()
    host = host.removeprefix("www.")
    return host + parts.path + (f"?{parts.query}" if parts.query else "")


def _bare_host(host: str) -> str:
    return host.removeprefix("www.")


def classify(website: str | None, social_domains: list[str]) -> Cls:
    if not website or not website.strip():
        return "no_website"
    try:
        _, host = _split(website)
    except ValueError:
        return "no_website"
    host = _bare_host(host)
    for d in social_domains:
        d = d.lower()
        if host == d or host.endswith("." + d):
            return "social_domain"
    return "crawl"


def _prefer(url: str) -> tuple:
    """R18: prefer https, then bare host, then shortest, then lexicographic."""
    return (not url.startswith("https://"), urlsplit(url).netloc.startswith("www."), len(url), url)


@dataclass
class Target:
    url: str
    place_ids: list[str]
    cls: str


def targets(con, run_id: str, crawl_cfg: CrawlConfig) -> list[Target]:
    rows = con.execute(
        "SELECT DISTINCT place_id, payload->>'websiteUri' FROM raw.places "
        "WHERE run_id = ? ORDER BY place_id, 2", [run_id]
    ).fetchall()
    out: list[Target] = []
    groups: dict[str, tuple[list[str], list[str]]] = {}  # key -> (urls, place_ids)
    seen: set[str] = set()
    for pid, raw in rows:
        if pid in seen:  # a place_id with differing websiteUri: take the first
            continue
        seen.add(pid)
        cls = classify(raw, crawl_cfg.social_domains)
        if cls == "no_website":
            out.append(Target("", [pid], cls))
        elif cls == "social_domain":
            out.append(Target(raw, [pid], cls))
        else:
            norm = normalise_url(raw)
            urls, pids = groups.setdefault(dedupe_key(norm), ([], []))
            urls.append(norm)
            pids.append(pid)
    for urls, pids in groups.values():
        out.append(Target(min(urls, key=_prefer), sorted(pids), "crawl"))
    out.sort(key=lambda t: (t.cls, t.url, t.place_ids))
    return out


@dataclass
class PageResult:
    final_url: str | None
    http_status: int | None
    markdown: str | None
    links: list[str] | None
    error: str | None
    retrieved_at: datetime
    from_cache: bool = False


def scrape(client: SourceClient, url: str) -> PageResult:
    """One Firecrawl scrape. ApiError and success:false become PageResult.error;
    AuthError propagates."""
    body = {"url": url, "formats": ["markdown", "links"], "onlyMainContent": False}
    try:
        resp = client.request("POST", FIRECRAWL_URL, json_body=body)
    except ApiError as e:
        return PageResult(None, None, None, None, f"ApiError {e}", datetime.now(UTC))
    try:
        payload = resp.json()
        ok = payload.get("success") is True
    except (ValueError, AttributeError):
        payload, ok = {}, False
    if not ok:
        err = str(payload.get("error", "success=false")) if isinstance(payload, dict) else "bad json"
        return PageResult(None, None, None, None, err, resp.fetched_at, resp.from_cache)
    data = payload.get("data") or {}
    meta = data.get("metadata") or {}
    return PageResult(
        meta.get("url") or url, meta.get("statusCode"), data.get("markdown"),
        data.get("links"), None, resp.fetched_at, resp.from_cache)


def page_rows(target: Target, role: str, requested_url: str | None,
              result: PageResult | None, skip_reason: str | None) -> list[dict]:
    """One homogeneous web_pages row per place_id of the target."""
    md = result.markdown if result else None
    sha = hashlib.sha256(md.encode("utf-8")).hexdigest() if md is not None else None
    links = result.links if result and role == "home" else None
    return [{
        "retrieved_at": result.retrieved_at if result else datetime.now(UTC),
        "place_id": pid, "page_role": role, "requested_url": requested_url or None,
        "final_url": result.final_url if result else None,
        "http_status": result.http_status if result else None,
        "skip_reason": skip_reason, "markdown": md, "links": links, "content_sha256": sha,
    } for pid in target.place_ids]


def run_crawl(con, fc_client: SourceClient, robots_client: SourceClient, config: AppConfig,
              run, max_pages: int):
    """Scrape each target's homepage. max_pages caps Firecrawl network sends (cache hits free)."""
    with step(con, run, "crawl") as stats:
        checker = RobotsChecker(robots_client, config.crawl.user_agent)
        pages_sent = 0
        capped = False
        unreachable = 0
        try:
            for t in targets(con, run.run_id, config.crawl):
                if t.cls != "crawl":
                    insert_rows(con, "web_pages",
                                page_rows(t, "home", t.url, None, t.cls), run)
                    continue
                if pages_sent >= max_pages:  # the single cap check; extend for legal pages here
                    capped = True
                    insert_rows(con, "web_pages", page_rows(t, "home", t.url, None, "capped"), run)
                    continue
                ok, why = checker.allowed(t.url)
                if not ok:
                    unreachable += why == "robots_unreachable"
                    insert_rows(con, "web_pages",
                                page_rows(t, "home", t.url, None, "robots_disallow"), run)
                    continue
                res = scrape(fc_client, t.url)
                if not res.from_cache:
                    pages_sent += 1
                if res.error:
                    stats.items_failed += 1
                    insert_rows(con, "web_pages", page_rows(t, "home", t.url, None, "error"), run)
                    continue
                stats.items_ok += 1
                insert_rows(con, "web_pages", page_rows(t, "home", t.url, res, None), run)
        finally:
            stats.api_requests = fc_client.api_requests + robots_client.api_requests
            stats.cache_hits = fc_client.cache_hits + robots_client.cache_hits
        if unreachable:
            stats.notes.append(f"robots_unreachable={unreachable}")
        if capped:
            stats.status = "capped"
            stats.notes.append(f"firecrawl_pages_cap={max_pages}")
    return stats
