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


def test_missing_google_key_fails(monkeypatch):
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.setenv("FIRECRAWL_API_KEY", "fc-x")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


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
