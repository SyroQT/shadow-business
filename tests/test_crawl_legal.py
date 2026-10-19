import json

import httpx
import pytest

from src.extract.crawl import markdown_anchors, select_legal_links
from tests.replay import load_recordings
from tests.test_crawl_scrape import Env, fc_ok

PAT = "kontakt|rekvizit|apie|about|contact|privatum|privacy|taisykl|terms|imone|company"
H = "https://a.lt/"


def sel(links, anchors=None, home=H, k=2):
    return select_legal_links(home, links, anchors or {}, PAT, k)


def test_select_prefers_rekvizit_then_kontakt():
    links = ["https://a.lt/apie", "https://a.lt/kontaktai", "https://a.lt/rekvizitai",
             "https://a.lt/privatumo-politika", "https://facebook.com/x",
             "https://other.lt/kontaktai"]
    assert sel(links) == ["https://a.lt/rekvizitai", "https://a.lt/kontaktai"]


def test_anchor_text_match():
    links = ["https://a.lt/kontaktai", "https://a.lt/p/12"]
    out = sel(links, {"https://a.lt/p/12": "Įmonės rekvizitai"})
    assert out[0] == "https://a.lt/p/12"


def test_www_and_trailing_variants_same_domain():
    links = ["http://www.a.lt/kontaktai/", "https://A.lt/apie?utm_x=1", "https://b.a.lt/kontaktai"]
    out = sel(links, home="https://www.a.lt/")
    assert out == ["http://www.a.lt/kontaktai/", "https://a.lt/apie"]


def test_homepage_and_fragment_links_excluded():
    assert sel(["https://a.lt/#kontaktai", "https://www.a.lt/", "https://a.lt/#x"]) == []


def test_non_http_and_dupes():
    out = sel(["mailto:a@a.lt", "https://a.lt/kontaktai", "https://a.lt/kontaktai#x"])
    assert out == ["https://a.lt/kontaktai"]


def test_no_matches_returns_empty():
    assert sel(["https://a.lt/produktai", "https://a.lt/blog"]) == []


def test_markdown_anchors_resolve_and_first_wins():
    md = '[Kontaktai](/kontaktai) text [Other](https://a.lt/kontaktai "t") [X](rel/p)'
    a = markdown_anchors(md, "https://a.lt/en/")
    assert a["https://a.lt/kontaktai"] == "Kontaktai"
    assert a["https://a.lt/en/rel/p"] == "X"


def make_fc(hosts_links):
    def fc(body):
        u = body["url"]
        d = fc_ok(u)
        d["data"]["links"] = hosts_links.get(u, [])
        return httpx.Response(200, json=d)
    return fc


def two_links(host):
    return [f"https://{host}/rekvizitai", f"https://{host}/kontaktai"]


def cfg_legal(monkeypatch):
    import tests.test_crawl_scrape as m
    orig = m.cfg

    def c():
        x = orig()
        x.crawl.legal_link_pattern = PAT
        return x
    monkeypatch.setattr(m, "cfg", c)


def test_legal_pages_scraped_with_rows(con, monkeypatch):
    cfg_legal(monkeypatch)
    e = Env(con, [("p1", "https://a.lt/"), ("p2", "https://a.lt")],
            fc=make_fc({"https://a.lt/": two_links("a.lt")}))
    stats, rows = e.go()
    legal = [r for r in rows if r[1] == "legal_candidate"]
    assert len(e.fc_calls) == 3 and len(legal) == 4  # 2 pages x 2 place_ids
    assert {r[2] for r in legal} == {"https://a.lt/rekvizitai", "https://a.lt/kontaktai"}
    assert all(r[7] is None and r[6] for r in legal)
    assert stats.items_failed == 0


def test_legal_robots_disallow_and_error(con, monkeypatch):
    cfg_legal(monkeypatch)

    def fc(body):
        if body["url"].endswith("/kontaktai"):
            return httpx.Response(500, text="boom")
        d = fc_ok(body["url"])
        d["data"]["links"] = two_links("a.lt")
        return httpx.Response(200, json=d)
    e = Env(con, [("p1", "https://a.lt/")], fc=fc,
            robots=(200, "User-agent: *\nDisallow: /rekvizitai\n"))
    stats, rows = e.go()
    by = {r[2]: r for r in rows if r[1] == "legal_candidate"}
    assert by["https://a.lt/rekvizitai"][5] == "robots_disallow"
    assert by["https://a.lt/kontaktai"][5] == "error"
    assert stats.items_failed == 1
    assert "firecrawl_errors=1" in stats.notes and "firecrawl_error_top=500" in stats.notes


def test_cap_stops_cleanly(con, monkeypatch):
    cfg_legal(monkeypatch)
    e = Env(con, [("p1", "https://a.lt/"), ("p2", "https://b.lt/"), ("p3", "https://c.lt/")],
            fc=make_fc({f"https://{h}.lt/": two_links(f"{h}.lt") for h in "abc"}))
    stats, rows = e.go(max_pages=4)
    assert len(e.fc_calls) == 4
    assert [c["url"] for c in e.fc_calls][:3] == [
        "https://a.lt/", "https://a.lt/rekvizitai", "https://a.lt/kontaktai"]
    assert stats.status == "capped" and "firecrawl_pages_cap=4" in stats.notes
    by = {(r[0], r[1]): r[5] for r in rows}
    assert by[("p3", "home")] == "capped"
    assert not [r for r in rows if r[0] == "p2" and r[1] == "legal_candidate"]


def test_cap_not_flagged_when_all_fetched(con, monkeypatch):
    cfg_legal(monkeypatch)
    e = Env(con, [("p1", "https://a.lt/")], fc=make_fc({"https://a.lt/": two_links("a.lt")}))
    stats, _ = e.go(max_pages=3)
    assert len(e.fc_calls) == 3 and stats.status is None


def test_402_treated_as_cap(con, monkeypatch):
    cfg_legal(monkeypatch)

    def fc(body):
        if body["url"] == "https://a.lt/kontaktai" or body["url"] == "https://b.lt/":
            return httpx.Response(402, text="Payment Required")
        d = fc_ok(body["url"])
        d["data"]["links"] = two_links("a.lt")
        return httpx.Response(200, json=d)
    e = Env(con, [("p1", "https://a.lt/"), ("p2", "https://b.lt/"), ("p3", "https://c.lt/")],
            fc=fc)
    stats, rows = e.go()
    assert len(e.fc_calls) == 3  # a, a/rekvizitai, a/kontaktai(402) -> stop
    assert stats.status == "capped" and "firecrawl_402" in stats.notes
    assert stats.items_failed == 0
    by = {(r[0], r[1]): r[5] for r in rows}
    assert by[("p2", "home")] == "capped" and by[("p3", "home")] == "capped"


def test_402_on_home_gets_capped_row(con):
    e = Env(con, [("p1", "https://a.lt/"), ("p2", "https://b.lt/")],
            fc=lambda b: httpx.Response(402, text="no credits"))
    stats, rows = e.go()
    assert len(e.fc_calls) == 1
    assert [r[5] for r in rows] == ["capped", "capped"]
    assert "firecrawl_402" in stats.notes and stats.status == "capped"


def test_error_note_and_log(con, capsys):
    e = Env(con, [("p1", "https://a.lt/"), ("p2", "https://b.lt/")],
            fc=lambda b: httpx.Response(200, json={"success": False, "error": "x" * 300}))
    stats, _ = e.go()
    assert "firecrawl_errors=2" in stats.notes
    assert f"firecrawl_error_top={'x' * 40}" in stats.notes
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines()
              if '"crawl_page_error"' in line]
    assert len(events) == 2
    assert events[0]["url"] == "https://a.lt/" and len(events[0]["message"]) <= 200
    assert set(events[0]) >= {"ts", "event", "step", "run_id", "url", "http_status", "message"}


@pytest.mark.recorded
def test_recorded_homepages_legal_picks(capsys):
    recs = load_recordings("firecrawl").values()
    assert len(recs) == 10
    withpick = 0
    lines = []
    for r in recs:
        data = r["response"]["json"]["data"]
        home = data["metadata"]["url"]
        picks = select_legal_links(home, data.get("links") or [],
                                   markdown_anchors(data.get("markdown") or "", home), PAT)
        from urllib.parse import urlsplit

        from src.extract.crawl import normalise_url
        bare = lambda u: urlsplit(u).hostname.removeprefix("www.")  # noqa: E731
        assert len(picks) <= 2
        for p in picks:
            assert bare(p) == bare(home) and not urlsplit(p).fragment
            assert normalise_url(p) != normalise_url(home)
        withpick += bool(picks)
        lines.append(f"{home} -> {picks}")
    with capsys.disabled():
        print("\n" + "\n".join(lines))
    assert withpick >= 6
