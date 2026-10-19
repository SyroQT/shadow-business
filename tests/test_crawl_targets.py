import pytest

from src.db import insert_rows
from src.extract.crawl import Target, classify, dedupe_key, normalise_url, targets
from src.run import new_run
from src.settings import CrawlConfig

SOCIAL = ["facebook.com", "fb.com", "instagram.com", "linktr.ee", "wolt.com", "bolt.eu",
          "sites.google.com", "booksy.com", "page.link"]
CFG = CrawlConfig(user_agent="t", social_domains=SOCIAL, legal_link_pattern="x")


@pytest.mark.parametrize("url,expected", [
    (None, "no_website"),
    ("", "no_website"),
    ("not a url", "no_website"),
    ("mailto:a@b.lt", "no_website"),
    ("tel:+37060000000", "no_website"),
    ("https://www.facebook.com/kavine", "social_domain"),
    ("https://m.facebook.com/x", "social_domain"),
    ("https://instagram.com/x", "social_domain"),
    ("https://linktr.ee/x", "social_domain"),
    ("https://wolt.com/lt/ltu/vilnius/restaurant/x", "social_domain"),
    ("https://food.bolt.eu/x", "social_domain"),
    ("https://sites.google.com/view/x", "social_domain"),
    ("https://booksy.com/lt-lt/x", "social_domain"),
    ("https://treatwellapp.page.link/abc", "social_domain"),
    ("https://Kavine.LT/?utm_source=google#top", "crawl"),
    ("http://www.chain.lt", "crawl"),
    ("https://chain.lt/", "crawl"),
    ("https://notfacebook.com", "crawl"),
    ("kavine.lt", "crawl"),
])
def test_classify_samples(url, expected):
    assert classify(url, SOCIAL) == expected


def test_normalise_strips_tracking():
    assert normalise_url("https://Kavine.LT/?utm_source=g&b=2&a=1#top") == "https://kavine.lt/?a=1&b=2"


def test_normalise_misc():
    assert normalise_url("HTTP://X.lt:80") == "http://x.lt/"
    assert normalise_url("https://x.lt:443/a?fbclid=1&ref=2") == "https://x.lt/a"
    assert normalise_url("https://x.lt:8080/a") == "https://x.lt:8080/a"
    assert normalise_url("x.lt") == "https://x.lt/"
    with pytest.raises(ValueError):
        normalise_url("mailto:a@b.lt")


def test_dedupe_key():
    assert dedupe_key(normalise_url("http://www.x.lt")) == dedupe_key(normalise_url("https://x.lt/"))
    assert dedupe_key("https://x.lt/a?b=1") == "x.lt/a?b=1"


def _put(con, run, rows):
    insert_rows(con, "places", [
        {"category": c, "tile_id": "t", "request_hash": "h", "place_id": pid,
         "payload": ({"id": pid} | ({"websiteUri": w} if w else {}))}
        for c, pid, w in rows], run)


def test_chain_dedupe(con):
    run = new_run("2026-10")
    _put(con, run, [("cafe", "p1", "http://www.chain.lt"), ("cafe", "p2", "https://chain.lt/"),
                    ("cafe", "p3", "https://chain.lt/?utm_medium=x")])
    out = targets(con, run.run_id, CFG)
    assert out == [Target(url="http://www.chain.lt/", place_ids=["p1", "p2", "p3"], cls="crawl")]


def test_targets_classes_and_multicategory(con):
    run = new_run("2026-10")
    _put(con, run, [("cafe", "p1", "https://a.lt/"), ("bar", "p1", "https://a.lt/"),
                    ("cafe", "p2", None), ("cafe", "p3", "https://www.facebook.com/x")])
    other = new_run("2026-11")
    _put(con, other, [("cafe", "p9", "https://z.lt/")])
    out = targets(con, run.run_id, CFG)
    assert [(t.cls, t.url, t.place_ids) for t in out] == [
        ("crawl", "https://a.lt/", ["p1"]),
        ("no_website", "", ["p2"]),
        ("social_domain", "https://www.facebook.com/x", ["p3"]),
    ]
