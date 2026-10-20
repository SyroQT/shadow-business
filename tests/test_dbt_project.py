import csv
from pathlib import Path

import yaml

from src.db import connect
from tests.dbt_harness import build, rows

ROOT = Path(__file__).resolve().parent.parent


def test_seed_matches_categories_yaml():
    cfg = yaml.safe_load((ROOT / "config" / "categories.yaml").read_text())
    expected = {
        (cat, prefix)
        for cat, c in cfg["categories"].items()
        for prefix in c["evrk_prefixes"]
    }
    with (ROOT / "dbt" / "seeds" / "category_evrk.csv").open(newline="") as f:
        actual = {(r["category"], r["evrk_prefix"]) for r in csv.DictReader(f)}
    assert actual == expected


def test_build_on_empty_raw_db(tmp_path):
    db = tmp_path / "t.duckdb"
    connect(db).close()
    build(db)
    assert rows(db, "select count(*) from staging.category_evrk") == [(5,)]
