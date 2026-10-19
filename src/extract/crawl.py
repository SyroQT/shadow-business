"""Firecrawl over raw_places URLs -> raw_web_pages."""
import hashlib
import re
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

from src.cache import ApiError, AuthError, CapReached, SourceClient
from src.db import insert_rows, step
from src.extract.robots import RobotsChecker
from src.log import log
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
    error_status: int | None = None


def scrape(client: SourceClient, url: str) -> PageResult:
    """One Firecrawl scrape. ApiError, success:false and HTTP 403 (R23: UNSUPPORTED_SITE,
    a per-URL answer) become PageResult.error; a 401 AuthError and CapReached propagate."""
    body = {"url": url, "formats": ["markdown", "links"], "onlyMainContent": False}
    try:
        resp = client.request("POST", FIRECRAWL_URL, json_body=body)
    except ApiError as e:
        return PageResult(None, None, None, None, f"ApiError {e}", datetime.now(UTC),
                          error_status=e.status)
    except AuthError as e:
        if e.status != 403:
            raise
        return PageResult(None, None, None, None, f"HTTP 403: {e.body[:150]}",
                          datetime.now(UTC), error_status=403)
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


_ANCHOR = re.compile(r'\[([^\]]*)\]\((\S+?)(?:\s+"[^"]*")?\)')


def markdown_anchors(markdown: str, base_url: str | None = None) -> dict[str, str]:
    """Absolute URL -> anchor text; relative URLs resolve against base_url; first wins."""
    out: dict[str, str] = {}
    for text, href in _ANCHOR.findall(markdown or ""):
        url = urljoin(base_url, href) if base_url else href
        out.setdefault(url, text.strip())
    return out


def select_legal_links(home_url: str, links: list[str], anchors: dict[str, str],
                       pattern: str, k: int = 2) -> list[str]:
    """Up to k same-domain links whose path/anchor matches pattern; rekvizit > kontakt/contact > rest."""
    try:
        home_norm = normalise_url(home_url)
        home_host = _bare_host(_split(home_url)[1])
    except ValueError:
        return []
    pat = re.compile(pattern, re.I)
    seen: set[str] = set()
    ranked: list[tuple[int, int, str]] = []
    for i, link in enumerate(links or []):
        try:
            absolute = urljoin(home_url, link)
            norm = normalise_url(absolute)
            host = _bare_host(_split(norm)[1])
        except ValueError:
            continue
        if host != home_host or norm == home_norm or norm in seen:
            continue
        text = urlsplit(norm).path + " " + (
            anchors.get(link) or anchors.get(absolute) or anchors.get(norm) or "")
        if not pat.search(text):
            continue
        seen.add(norm)
        tier = 0 if re.search("rekvizit", text, re.I) else 1 if re.search("kontakt|contact", text, re.I) else 2
        ranked.append((tier, i, norm))
    ranked.sort()
    return [u for _, _, u in ranked[:k]]


def run_crawl(con, fc_client: SourceClient, robots_client: SourceClient, config: AppConfig,
              run, max_pages: int):
    """Scrape each target's homepage, then its legal candidates (depth-first per site).
    max_pages caps Firecrawl network attempts, retries included (cache hits free);
    a 402 acts like the cap."""
    with step(con, run, "crawl") as stats:
        checker = RobotsChecker(robots_client, config.crawl.user_agent)
        start_requests = fc_client.api_requests
        saved_max = fc_client.max_requests
        fc_client.max_requests = start_requests + max_pages  # retries cannot overshoot
        cache_start = fc_client.cache_hits
        robots_start = (robots_client.api_requests, robots_client.cache_hits)
        stop: str | None = None  # None | "cap" | "402"
        unreachable = 0
        errors: Counter[str] = Counter()

        def sent() -> int:
            return fc_client.api_requests - start_requests

        def fetch(url: str) -> PageResult:
            """Scrape url, count sends, record error stats. Sets stop on 402."""
            nonlocal stop
            try:
                res = scrape(fc_client, url)
            except CapReached:
                stop = "cap"
                return PageResult(None, None, None, None, "cap reached", datetime.now(UTC))
            if res.error_status == 402:
                stop = "402"
            elif res.error:
                stats.items_failed += 1
                errors[str(res.error_status) if res.error_status
                       else res.error[:40]] += 1
                log("crawl_page_error", step="crawl", run_id=run.run_id, url=url,
                    http_status=res.error_status, message=res.error[:200])
            return res

        try:
            for t in targets(con, run.run_id, config.crawl):
                if t.cls != "crawl":
                    insert_rows(con, "web_pages",
                                page_rows(t, "home", t.url, None, t.cls), run)
                    continue
                if stop is None and sent() >= max_pages:
                    stop = "cap"
                if stop:
                    insert_rows(con, "web_pages", page_rows(t, "home", t.url, None, "capped"), run)
                    continue
                ok, why = checker.allowed(t.url)
                if not ok:
                    unreachable += why == "robots_unreachable"
                    insert_rows(con, "web_pages",
                                page_rows(t, "home", t.url, None, "robots_disallow"), run)
                    continue
                res = fetch(t.url)
                if stop:
                    insert_rows(con, "web_pages", page_rows(t, "home", t.url, None, "capped"), run)
                    continue
                if res.error:
                    insert_rows(con, "web_pages", page_rows(t, "home", t.url, None, "error"), run)
                    continue
                stats.items_ok += 1
                insert_rows(con, "web_pages", page_rows(t, "home", t.url, res, None), run)

                home_url = res.final_url or t.url
                legal = select_legal_links(
                    home_url, res.links or [], markdown_anchors(res.markdown or "", home_url),
                    config.crawl.legal_link_pattern, config.crawl.max_legal_links)
                for lurl in legal:
                    if sent() >= max_pages:
                        stop = "cap"
                        break
                    ok, why = checker.allowed(lurl)
                    if not ok:
                        unreachable += why == "robots_unreachable"
                        insert_rows(con, "web_pages", page_rows(
                            t, "legal_candidate", lurl, None, "robots_disallow"), run)
                        continue
                    lres = fetch(lurl)
                    if stop:
                        break
                    if lres.error:
                        insert_rows(con, "web_pages", page_rows(
                            t, "legal_candidate", lurl, None, "error"), run)
                        continue
                    insert_rows(con, "web_pages", page_rows(
                        t, "legal_candidate", lurl, lres, None), run)
        finally:
            fc_client.max_requests = saved_max
            stats.api_requests = sent()
            stats.cache_hits = fc_client.cache_hits - cache_start
            stats.notes.append(f"robots_requests={robots_client.api_requests - robots_start[0]}")
            stats.notes.append(f"robots_cache_hits={robots_client.cache_hits - robots_start[1]}")
        if unreachable:
            stats.notes.append(f"robots_unreachable={unreachable}")
        if errors:
            stats.notes.append(f"firecrawl_errors={sum(errors.values())}")
            stats.notes.append(f"firecrawl_error_top={errors.most_common(1)[0][0]}")
        if stop:
            stats.status = "capped"
            stats.notes.append("firecrawl_402" if stop == "402" else f"firecrawl_pages_cap={max_pages}")
    return stats
