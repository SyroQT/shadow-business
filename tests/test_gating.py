from tests.conftest import live_enabled, recorded_available


def test_live_only_with_live_1():
    assert live_enabled({"LIVE": "1"})
    assert not live_enabled({}) and not live_enabled({"LIVE": "0"})


def test_recorded_needs_json_files(tmp_path):
    assert not recorded_available(tmp_path / "missing")
    assert not recorded_available(tmp_path)
    (tmp_path / "a.json").write_text("{}")
    assert recorded_available(tmp_path)
