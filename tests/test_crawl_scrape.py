import hashlib
import json

import httpx
import pytest

from src.cache import ApiError, AuthError, SourceClient, TokenBucket
from src.db import insert_rows
from src.extract.crawl import FIRECRAWL_URL, RobotsChecker, run_crawl, scrape
from src.run import new_run
from src.settings import AppConfig
from tests.replay import RECORDED, load_recordings, replay_transport

UA = "TestBot"
ALLOW = "User-agent: *\nAllow: /\n"
DISALLOW = "User-agent: *\nDisallow: /\n"


def cfg() -> AppConfig:
    return AppConfig.model_validate({
        "area": {"name": "t", "bbox": {"south": 0, "west": 0, "north": 1, "east": 1},
                 "tile_km": 1.0, "min_tile_km": 0.5},
        "categories": {"cafe": {"places_included_type": ["cafe"], "evrk_prefixes": []}},
        "crawl": {"user_agent": UA, "social_domains": ["facebook.com"], "legal_link_pattern": "zzzz"},
    })


def client(handler, name="x"):
    return SourceClient(name, None, TokenBucket(1000, sleep=lambda s: None), None, {},
                        transport=httpx.MockTransport(handler), sleep=lambda s: None,
                        follow_redirects=True)


def fc_ok(url="https://a.lt/", status=200, md="# Hi\nkodas 123", final=None):
    return {"success": True, "data": {
        "markdown": md, "links": ["https://a.lt/x"],
        "metadata": {"url": final or url, "sourceURL": url, "statusCode": status}}}


class Env:
    def __init__(self, con, places, robots=(200, ALLOW), fc=None):
        self.con, self.run = con, new_run("2026-10")
        insert_rows(con, "places", [
            {"category": "cafe", "tile_id": "t", "request_hash": "h", "place_id": pid,
             "payload": {"id": pid} | ({"websiteUri": w} if w else {})}
            for pid, w in places], self.run)
        self.fc_calls, self.robots_calls = [], []
        fc = fc or (lambda body: httpx.Response(200, json=fc_ok(body["url"])))

        def fc_h(req):
            body = json.loads(req.content)
            self.fc_calls.append(body)
            return fc(body)

        def rb_h(req):
            self.robots_calls.append(str(req.url))
            status, text = robots
            return httpx.Response(status, text=text)

        self.fc, self.rb = client(fc_h, "firecrawl"), client(rb_h, "robots")

    def go(self, max_pages=100):
        stats = run_crawl(self.con, self.fc, self.rb, cfg(), self.run, max_pages)
        rows = self.con.execute(
            "SELECT place_id, page_role, requested_url, final_url, http_status, skip_reason, "
            "markdown, links, content_sha256 FROM raw.web_pages ORDER BY place_id").fetchall()
        return stats, rows


def test_robots_allow_then_scrape(con):
    e = Env(con, [("p1", "https://a.lt/")])
    stats, rows = e.go()
    r = rows[0]
    assert r[1] == "home" and r[2] == "https://a.lt/" and r[3] == "https://a.lt/"
    assert r[4] == 200 and r[5] is None and r[6].startswith("# Hi")
    assert len(r[8]) == 64 and r[8] == hashlib.sha256(r[6].encode()).hexdigest()
    assert json.loads(r[7]) == ["https://a.lt/x"]
    assert e.fc_calls == [{"url": "https://a.lt/", "formats": ["markdown", "links"],
                           "onlyMainContent": False}]
    assert stats.items_ok == 1 and stats.items_failed == 0
    assert stats.api_requests == 2  # firecrawl + robots


def test_robots_disallow_skips_without_firecrawl_call(con):
    e = Env(con, [("p1", "https://a.lt/")], robots=(200, DISALLOW))
    stats, rows = e.go()
    assert e.fc_calls == []
    assert rows[0][5] == "robots_disallow" and rows[0][6] is None and rows[0][8] is None
    assert rows[0][2] == "https://a.lt/"


def test_robots_404_allows(con):
    e = Env(con, [("p1", "https://a.lt/")], robots=(404, "nope"))
    _, rows = e.go()
    assert len(e.fc_calls) == 1 and rows[0][5] is None


@pytest.mark.parametrize("status", [500, 503])
def test_robots_5xx_disallows(con, status):
    e = Env(con, [("p1", "https://a.lt/")], robots=(status, "err"))
    stats, rows = e.go()
    assert e.fc_calls == []
    assert rows[0][5] == "robots_disallow"
    assert "robots_unreachable=1" in stats.notes


def test_robots_auth_error_allows(con):
    e = Env(con, [("p1", "https://a.lt/")], robots=(403, "forbidden"))
    _, rows = e.go()
    assert len(e.fc_calls) == 1 and rows[0][5] is None


def test_robots_checker_parser_cached_per_host(con):
    calls = []
    c = client(lambda r: (calls.append(1), httpx.Response(200, text="User-agent: *\nDisallow: /private\n"))[1])
    rc = RobotsChecker(c, UA)
    assert rc.allowed("https://a.lt/") == (True, None)
    assert rc.allowed("https://a.lt/private/x") == (False, None)
    assert len(calls) == 1
    rc.allowed("http://a.lt/")
    assert len(calls) == 2


def test_robots_network_error_unreachable():
    def boom(req):
        raise httpx.ConnectError("down")
    rc = RobotsChecker(client(boom), UA)
    assert rc.allowed("https://a.lt/") == (False, "robots_unreachable")


def test_firecrawl_error_row(con):
    e = Env(con, [("p1", "https://a.lt/")], fc=lambda b: httpx.Response(500, text="boom"))
    stats, rows = e.go()
    assert rows[0][5] == "error" and rows[0][6] is None
    assert stats.items_failed == 1 and stats.items_ok == 0
    from_log = con.execute("SELECT status FROM raw.run_log WHERE step='crawl'").fetchone()
    assert from_log[0] == "partial"


def test_firecrawl_success_false_is_error(con):
    e = Env(con, [("p1", "https://a.lt/")],
            fc=lambda b: httpx.Response(200, json={"success": False, "error": "x"}))
    stats, rows = e.go()
    assert rows[0][5] == "error" and stats.items_failed == 1


def test_target_404_lands_with_status(con):
    e = Env(con, [("p1", "https://a.lt/kava/")],
            fc=lambda b: httpx.Response(200, json=fc_ok(b["url"], status=404, md="not found page")))
    stats, rows = e.go()
    assert rows[0][4] == 404 and rows[0][6] == "not found page" and rows[0][5] is None
    assert stats.items_ok == 1


def test_final_url_from_metadata(con):
    e = Env(con, [("p1", "https://a.lt/")],
            fc=lambda b: httpx.Response(200, json=fc_ok(b["url"], final="https://b.lt/en")))
    _, rows = e.go()
    assert rows[0][2] == "https://a.lt/" and rows[0][3] == "https://b.lt/en"


def test_auth_error_fails_step(con):
    e = Env(con, [("p1", "https://a.lt/")], fc=lambda b: httpx.Response(401, text="bad key"))
    with pytest.raises(AuthError):
        e.go()
    assert con.execute("SELECT status FROM raw.run_log WHERE step='crawl'").fetchone()[0] == "failed"


def test_chain_lands_row_per_place(con):
    e = Env(con, [("p1", "http://www.chain.lt"), ("p2", "https://chain.lt/"),
                  ("p3", "https://chain.lt/?utm_x=1")])
    stats, rows = e.go()
    assert len(e.fc_calls) == 1 and len(rows) == 3
    assert [r[0] for r in rows] == ["p1", "p2", "p3"]
    assert len({r[8] for r in rows}) == 1
    assert stats.items_ok == 1


def test_no_website_row_written(con):
    e = Env(con, [("p1", None), ("p2", "https://facebook.com/x"), ("p3", "https://a.lt/")])
    stats, rows = e.go()
    by = {r[0]: r for r in rows}
    assert len(rows) == 3
    assert by["p1"][5] == "no_website" and by["p1"][2] is None and by["p1"][6] is None
    assert by["p2"][5] == "social_domain" and by["p2"][2] == "https://facebook.com/x"
    assert by["p3"][5] is None
    assert len(e.fc_calls) == 1


def test_cap_stops_scraping_and_marks_capped(con):
    e = Env(con, [("p1", "https://a.lt/"), ("p2", "https://b.lt/"), ("p3", "https://c.lt/"),
                  ("p4", None)])
    stats, rows = e.go(max_pages=2)
    by = {r[0]: r for r in rows}
    assert len(e.fc_calls) == 2
    assert by["p1"][5] is None and by["p2"][5] is None
    assert by["p3"][5] == "capped" and by["p4"][5] == "no_website"
    assert stats.status == "capped" and "firecrawl_pages_cap=2" in stats.notes
    assert con.execute("SELECT status FROM raw.run_log WHERE step='crawl'").fetchone()[0] == "capped"


def test_cache_hits_do_not_count_toward_cap(con, tmp_path):
    from src.cache import DiskCache
    e = Env(con, [("p1", "https://a.lt/"), ("p2", "https://b.lt/")])
    e.fc.cache = DiskCache(tmp_path / "c", None)
    e.go(max_pages=1)  # p1 sent, p2 capped
    con.execute("DELETE FROM raw.web_pages")
    stats, rows = e.go(max_pages=1)  # p1 from cache, p2 sent
    assert [r[5] for r in rows] == [None, None] and stats.status is None


def test_scrape_returns_page_result():
    c = client(lambda r: httpx.Response(200, json=fc_ok("https://a.lt/", status=404)))
    res = scrape(c, "https://a.lt/")
    assert res.http_status == 404 and res.final_url == "https://a.lt/" and res.error is None
    assert FIRECRAWL_URL == "https://api.firecrawl.dev/v2/scrape"


@pytest.mark.recorded
def test_recorded_homepages(con):
    recs = list(load_recordings("firecrawl").values())
    assert len(recs) == 10
    run = new_run("2026-10")
    insert_rows(con, "places", [
        {"category": r["meta"]["category"], "tile_id": "t", "request_hash": "h",
         "place_id": r["meta"]["place_id"],
         "payload": {"id": r["meta"]["place_id"], "websiteUri": r["request"]["body"]["url"]}}
        for r in recs], run)
    fc = SourceClient("firecrawl", None, TokenBucket(1000, sleep=lambda s: None), None, {},
                      transport=replay_transport("firecrawl"), sleep=lambda s: None)
    rb = SourceClient("robots", None, TokenBucket(1000, sleep=lambda s: None), None, {},
                      transport=replay_transport("robots"), sleep=lambda s: None,
                      follow_redirects=True)
    stats = run_crawl(con, fc, rb, cfg(), run, 100)
    rows = con.execute("SELECT place_id, page_role, http_status, skip_reason, markdown, "
                       "final_url, requested_url FROM raw.web_pages").fetchall()
    assert len(rows) == 10 and all(r[1] == "home" and r[4] for r in rows)
    esk = [r for r in rows if "eskedar" in r[6]][0]
    assert esk[2] == 404 and esk[3] is None and esk[4]
    assert sum("kodas" in r[4] for r in rows) >= 3
    assert stats.items_ok == 10 and stats.items_failed == 0


@pytest.mark.live
def test_live_five_sites():
    pytest.skip("live: run manually with LIVE=1 (spends 5 Firecrawl credits)")


def test_robots_bom_disallow_all():
    body = "﻿User-agent: *\nDisallow: /\n".encode()
    rc = RobotsChecker(client(lambda r: httpx.Response(200, content=body)), UA)
    assert rc.allowed("https://a.lt/") == (False, None)
