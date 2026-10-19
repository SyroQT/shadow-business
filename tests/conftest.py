import os
from collections.abc import Mapping
from pathlib import Path

import pytest

RECORDED = Path(__file__).parent / "fixtures" / "recorded"


def live_enabled(env: Mapping[str, str]) -> bool:
    return env.get("LIVE") == "1"


def recorded_available(path: Path) -> bool:
    return path.is_dir() and any(path.glob("*.json"))


def pytest_collection_modifyitems(config, items):
    skip_live = pytest.mark.skip(reason="set LIVE=1 to run live tests")
    skip_recorded = pytest.mark.skip(reason="no recorded fixtures in tests/fixtures/recorded/")
    for item in items:
        if "live" in item.keywords and not live_enabled(os.environ):
            item.add_marker(skip_live)
        if "recorded" in item.keywords and not recorded_available(RECORDED):
            item.add_marker(skip_recorded)


@pytest.fixture
def con(tmp_path):
    from src.db import connect

    c = connect(tmp_path / "sub" / "t.duckdb")
    yield c
    c.close()
