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


def test_profile_fallback_is_repo_root_relative(tmp_path):
    # Run from a scratch cwd so the real DB is never opened; the fallback is
    # cwd-relative, so it must be exactly data/leads.duckdb (same as settings).
    import os
    import subprocess
    import sys

    from src.transform import DBT_DIR

    env = {k: v for k, v in os.environ.items() if k != "DB_PATH"}
    res = subprocess.run(
        [str(Path(sys.executable).parent / "dbt"), "debug",
         "--project-dir", str(DBT_DIR), "--profiles-dir", str(DBT_DIR)],
        cwd=tmp_path, env=env, capture_output=True, text=True,
    )
    assert "path: data/leads.duckdb" in res.stdout, res.stdout
    assert not (ROOT / "dbt" / "data").exists()
