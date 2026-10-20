from datetime import UTC, datetime

import pytest

from src.run import new_run


def test_run_id_format():
    r = new_run("2026-10", now=datetime(2026, 10, 19, 14, 15, 2, tzinfo=UTC))
    assert r.run_id == "2026-10_20261019T141502" and r.run_month == "2026-10"


def test_run_month_defaults_to_current_utc_month():
    assert new_run(None, now=datetime(2026, 1, 31, 23, 0, tzinfo=UTC)).run_month == "2026-01"


@pytest.mark.parametrize("bad", ["2026-13", "202610", "26-10"])
def test_bad_run_month_rejected(bad):
    with pytest.raises(ValueError):
        new_run(bad)
