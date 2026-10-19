from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from src.settings import Settings, load_config, main


def test_inverted_bbox_fails(tmp_path):
    cfg = yaml.safe_load(Path("config/categories.yaml").read_text())
    cfg["area"]["bbox"].update(south=54.9, north=54.5)
    p = tmp_path / "c.yaml"
    p.write_text(yaml.safe_dump(cfg))
    with pytest.raises(ValidationError):
        load_config(p)


def test_tile_smaller_than_min_fails(tmp_path):
    cfg = yaml.safe_load(Path("config/categories.yaml").read_text())
    cfg["area"]["tile_km"] = 0.1
    p = tmp_path / "c.yaml"
    p.write_text(yaml.safe_dump(cfg))
    with pytest.raises(ValidationError):
        load_config(p)


def test_keys_optional_at_load(monkeypatch):
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("FIRECRAWL_API_KEY", raising=False)
    s = Settings(_env_file=None)
    assert s.google_api_key is None and s.firecrawl_api_key is None


def test_client_factories_require_key(tmp_path):
    from src.cache import firecrawl_client, places_client
    s = Settings(_env_file=None, cache_dir=tmp_path)
    with pytest.raises(ValueError, match="GOOGLE_API_KEY is not set"):
        places_client(s)
    with pytest.raises(ValueError, match="FIRECRAWL_API_KEY is not set"):
        firecrawl_client(s)


def test_firecrawl_client_timeout_90(tmp_path):
    from src.cache import firecrawl_client
    s = Settings(_env_file=None, cache_dir=tmp_path, firecrawl_api_key="f")
    assert firecrawl_client(s).timeout == 90


def _clear_keys(monkeypatch):
    for k in ("GOOGLE_API_KEY", "FIRECRAWL_API_KEY"):
        monkeypatch.delenv(k, raising=False)


def test_validation_error_hides_secret_values(monkeypatch):
    _clear_keys(monkeypatch)
    monkeypatch.setenv("FIRECRAWL_API_KEY", "fc-SECRETVALUE123")
    monkeypatch.setenv("MAX_PLACES_REQUESTS", "not-a-number")
    with pytest.raises(ValidationError) as e:
        Settings(_env_file=None)
    assert "SECRETVALUE123" not in str(e.value)


def test_cli_prints_only_field_names(monkeypatch, capsys):
    from src.settings import settings_error_text
    _clear_keys(monkeypatch)
    monkeypatch.setenv("FIRECRAWL_API_KEY", "fc-SECRETVALUE123")
    monkeypatch.setenv("MAX_PLACES_REQUESTS", "not-a-number")
    monkeypatch.setattr("src.settings.Settings.model_config",
                        {**Settings.model_config, "env_file": None})
    with pytest.raises(ValidationError) as e:
        Settings(_env_file=None)
    assert settings_error_text(e.value) == "invalid setting: MAX_PLACES_REQUESTS"
    with pytest.raises(SystemExit) as ex:
        main()
    assert ex.value.code == 2
    cap = capsys.readouterr()
    assert "SECRETVALUE123" not in cap.out + cap.err
    assert "MAX_PLACES_REQUESTS" in cap.err


def test_config_loads_repo_yaml():
    cfg = load_config(Path("config/categories.yaml"))
    assert set(cfg.categories) == {"cafes_restaurants", "hair_beauty", "car_service"}


def test_masked_print_hides_keys(capsys, monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "AIzaSECRET123")
    monkeypatch.setenv("FIRECRAWL_API_KEY", "fc-SECRET456")
    monkeypatch.setattr("src.settings.Settings.model_config",
                        {**Settings.model_config, "env_file": None})
    main()
    out = capsys.readouterr().out
    assert "AIza…" in out and "fc-S…" in out
    assert "SECRET" not in out
