"""Firecrawl over raw_places URLs -> raw_web_pages."""
import re
from dataclasses import dataclass
from typing import Literal
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from src.settings import CrawlConfig

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
