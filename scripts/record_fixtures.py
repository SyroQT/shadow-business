"""Small live demo run that records every API call as a replayable fixture.

    uv run python scripts/record_fixtures.py places      # <= 10 Places requests
    uv run python scripts/record_fixtures.py firecrawl   # <= 10 Firecrawl scrapes

Each call is saved as tests/fixtures/recorded/{source}/{label}__{hash12}.json,
keyed by request_hash (method + URL + canonical JSON body; headers and API keys
are never stored). Replay them with tests/replay.py::replay_transport.
"""

import hashlib
import json
import os
import sys
import urllib.robotparser
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

import httpx

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "tests" / "fixtures" / "recorded"
USER_AGENT = "LeadsResearchBot/0.1"

PLACES_URL = "https://places.googleapis.com/v1/places:searchText"
FIELD_MASK = ",".join([
    "places.id", "places.displayName", "places.formattedAddress", "places.location",
    "places.types", "places.primaryType", "places.websiteUri", "places.userRatingCount",
    "places.rating", "places.priceLevel", "places.regularOpeningHours",
    "places.businessStatus", "places.googleMapsUri", "nextPageToken",
])
MAX_PLACES_REQUESTS = 10

FIRECRAWL_URL = "https://api.firecrawl.dev/v2/scrape"
MAX_FIRECRAWL_PAGES = 10

# (category, includedType, tile_id, (south, west, north, east), max_pages)
OLD_TOWN = (54.6790, 25.2760, 54.6880, 25.2910)          # ~1 km, dense
CENTRE_2KM = (54.6780, 25.2600, 54.6960, 25.2910)        # ~2 km
NAUJAMIESTIS_2KM = (54.6650, 25.2350, 54.6830, 25.2660)  # ~2 km, car services
PLACES_PLAN = [
    ("cafes_restaurants", "cafe", "demo_oldtown_1km", OLD_TOWN, 3),
    ("cafes_restaurants", "restaurant", "demo_oldtown_1km", OLD_TOWN, 3),
    ("hair_beauty", "hair_salon", "demo_centre_2km", CENTRE_2KM, 2),
    ("car_service", "car_repair", "demo_naujamiestis_2km", NAUJAMIESTIS_2KM, 2),
]

SOCIAL_DOMAINS = [
    "facebook.com", "fb.com", "instagram.com", "linktr.ee", "wolt.com", "bolt.eu",
    "tiktok.com", "sites.google.com", "business.site", "g.page", "youtube.com",
    "linkedin.com", "booksy.com", "fresha.com", "treatwell.lt", "manobooking.lt", "page.link",
]


def request_hash(method: str, url: str, body: dict | None) -> str:
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(f"{method.upper()}\n{url}\n{canonical}".encode()).hexdigest()


def load_env() -> dict[str, str]:
    env = dict(os.environ)
    path = ROOT / ".env"
    if path.exists():
        for line in path.read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                env.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    return env


def log(event: str, **fields) -> None:
    print(json.dumps({"ts": datetime.now(UTC).isoformat(), "event": event, **fields}), flush=True)


def save(source: str, label: str, method: str, url: str, body: dict | None,
         resp: httpx.Response, **meta) -> Path:
    h = request_hash(method, url, body)
    try:
        payload = {"json": resp.json()}
    except ValueError:
        payload = {"text": resp.text}
    record = {
        "source": source,
        "label": label,
        "request_hash": h,
        "recorded_at": datetime.now(UTC).isoformat(),
        "request": {"method": method.upper(), "url": url, "body": body},
        "response": {
            "status": resp.status_code,
            "headers": {"content-type": resp.headers.get("content-type", "")},
            **payload,
        },
        **({"meta": meta} if meta else {}),
    }
    path = OUT / source / f"{label}__{h[:12]}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n")
    return path


def rectangle(bounds: tuple[float, float, float, float]) -> dict:
    s, w, n, e = bounds
    return {"rectangle": {"low": {"latitude": s, "longitude": w},
                          "high": {"latitude": n, "longitude": e}}}


def record_places(env: dict[str, str]) -> None:
    key = env.get("GOOGLE_API_KEY") or env.get("GOOGLE_PLACES")
    if not key:
        sys.exit("GOOGLE_API_KEY (or GOOGLE_PLACES) missing from .env")
    headers = {"X-Goog-Api-Key": key, "X-Goog-FieldMask": FIELD_MASK}
    sent = 0
    with httpx.Client(timeout=30, headers=headers) as client:
        for category, included_type, tile_id, bounds, max_pages in PLACES_PLAN:
            token = None
            for page in range(1, max_pages + 1):
                if sent >= MAX_PLACES_REQUESTS:
                    log("places_cap_reached", sent=sent)
                    return
                body = {
                    "textQuery": included_type.replace("_", " "),
                    "includedType": included_type,
                    "strictTypeFiltering": True,
                    "pageSize": 20,
                    "languageCode": "lt",
                    "regionCode": "LT",
                    "locationRestriction": rectangle(bounds),
                }
                if token:
                    body["pageToken"] = token
                resp = client.post(PLACES_URL, json=body)
                sent += 1
                label = f"{included_type}_{tile_id}_p{page}"
                path = save("places", label, "POST", PLACES_URL, body, resp,
                            category=category, tile_id=tile_id, page=page)
                data = resp.json() if resp.is_success else {}
                log("places_request", label=label, status=resp.status_code,
                    places=len(data.get("places", [])), next=bool(data.get("nextPageToken")),
                    file=str(path.relative_to(ROOT)), sent=sent)
                if resp.status_code in (401, 403):
                    sys.exit(f"auth error {resp.status_code}: {resp.text[:300]}")
                token = data.get("nextPageToken")
                if not resp.is_success or not token:
                    break
    log("places_done", sent=sent)


def is_social(host: str) -> bool:
    return any(host == d or host.endswith("." + d) for d in SOCIAL_DOMAINS)


def site_key(url: str) -> str:
    host = (urlsplit(url).hostname or "").lower()
    return host.removeprefix("www.")


def firecrawl_candidates() -> list[dict]:
    """Places with a non-social website from the recorded Places fixtures,
    one per site, round-robin across categories for a mixed sample."""
    by_cat: dict[str, list[dict]] = {}
    for f in sorted((OUT / "places").glob("*.json")):
        rec = json.loads(f.read_text())
        cat = rec["meta"]["category"]
        for p in rec["response"].get("json", {}).get("places", []):
            url = p.get("websiteUri")
            if url and site_key(url) and not is_social(site_key(url)):
                by_cat.setdefault(cat, []).append(
                    {"category": cat, "place_id": p["id"], "name": p["displayName"]["text"], "url": url})
    seen, out = set(), []
    queues = [by_cat[c] for c in sorted(by_cat)]
    while any(queues):
        for q in queues:
            while q:
                c = q.pop(0)
                if site_key(c["url"]) not in seen:
                    seen.add(site_key(c["url"]))
                    out.append(c)
                    break
    return out


def robots_allows(client: httpx.Client, url: str) -> tuple[bool, str]:
    parts = urlsplit(url)
    robots_url = f"{parts.scheme}://{parts.netloc}/robots.txt"
    try:
        resp = client.get(robots_url, headers={"User-Agent": USER_AGENT}, follow_redirects=True)
    except httpx.HTTPError as e:
        return False, f"robots_unreachable: {type(e).__name__}"
    save("robots", site_key(url).replace(".", "_"), "GET", robots_url, None, resp)
    if resp.status_code >= 500:
        return False, f"robots_{resp.status_code}"
    if resp.status_code >= 400:
        return True, f"robots_{resp.status_code}_allow_all"
    rp = urllib.robotparser.RobotFileParser()
    rp.parse(resp.text.splitlines())
    return rp.can_fetch(USER_AGENT, url), "robots_parsed"


def record_firecrawl(env: dict[str, str]) -> None:
    key = env.get("FIRECRAWL_API_KEY")
    if not key:
        sys.exit("FIRECRAWL_API_KEY missing from .env")
    candidates = firecrawl_candidates()
    log("firecrawl_candidates", count=len(candidates))
    sent = 0
    with httpx.Client(timeout=90) as robots_client, \
         httpx.Client(timeout=90, headers={"Authorization": f"Bearer {key}"}) as fc:
        for c in candidates:
            if sent >= MAX_FIRECRAWL_PAGES:
                break
            allowed, why = robots_allows(robots_client, c["url"])
            if not allowed:
                log("robots_skip", url=c["url"], reason=why)
                continue
            body = {"url": c["url"], "formats": ["markdown", "links"], "onlyMainContent": False}
            resp = fc.post(FIRECRAWL_URL, json=body)
            sent += 1
            path = save("firecrawl", f"home_{site_key(c['url']).replace('.', '_')}", "POST",
                        FIRECRAWL_URL, body, resp, place_id=c["place_id"],
                        category=c["category"], name=c["name"])
            data = resp.json().get("data", {}) if resp.is_success else {}
            log("firecrawl_scrape", url=c["url"], status=resp.status_code,
                page_status=data.get("metadata", {}).get("statusCode"),
                markdown_chars=len(data.get("markdown") or ""), links=len(data.get("links") or []),
                file=str(path.relative_to(ROOT)), sent=sent)
            if resp.status_code in (401, 402, 403):
                sys.exit(f"firecrawl {resp.status_code}: {resp.text[:300]}")
    log("firecrawl_done", sent=sent)


if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in ("places", "firecrawl"):
        sys.exit(__doc__)
    env = load_env()
    {"places": record_places, "firecrawl": record_firecrawl}[sys.argv[1]](env)
