import os
from pathlib import Path

import pytest

RECORDED = Path(__file__).parent / "fixtures" / "recorded"


def pytest_collection_modifyitems(config, items):
    skip_live = pytest.mark.skip(reason="set LIVE=1 to run live tests")
    skip_recorded = pytest.mark.skip(reason="tests/fixtures/recorded/ missing")
    for item in items:
        if "live" in item.keywords and os.environ.get("LIVE") != "1":
            item.add_marker(skip_live)
        if "recorded" in item.keywords and not RECORDED.exists():
            item.add_marker(skip_recorded)
