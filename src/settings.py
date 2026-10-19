"""Typed env settings and category/area config."""
import json
import sys
from pathlib import Path

import yaml
from pydantic import BaseModel, Field, SecretStr, ValidationError, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_ignore_empty=True, extra="ignore",
                                      hide_input_in_errors=True)

    google_api_key: SecretStr | None = None
    firecrawl_api_key: SecretStr | None = None
    run_month: str | None = None
    max_places_requests: int = 1000
    max_firecrawl_pages: int = 600
    places_rate_per_sec: float = 5
    firecrawl_rate_per_min: float = 10
    sodra_url: str | None = None
    sodra_file: Path | None = None
    db_path: Path = Path("data/leads.duckdb")
    cache_dir: Path = Path("data/cache")
    config_path: Path = Path("config/categories.yaml")


class BBox(BaseModel):
    south: float
    west: float
    north: float
    east: float

    @model_validator(mode="after")
    def _check(self):
        if not (-90 <= self.south < self.north <= 90):
            raise ValueError("need -90 <= south < north <= 90")
        if not (-180 <= self.west < self.east <= 180):
            raise ValueError("need -180 <= west < east <= 180")
        return self


class Area(BaseModel):
    name: str
    bbox: BBox
    tile_km: float
    min_tile_km: float = 0.5

    @model_validator(mode="after")
    def _check(self):
        if self.tile_km < self.min_tile_km:
            raise ValueError("tile_km must be >= min_tile_km")
        return self


class Category(BaseModel):
    places_included_type: list[str] = Field(min_length=1)
    evrk_prefixes: list[str]


class CrawlConfig(BaseModel):
    user_agent: str
    social_domains: list[str]
    legal_link_pattern: str
    max_legal_links: int = 2


class SodraConfig(BaseModel):
    url_pattern: str | None = None


class AppConfig(BaseModel):
    area: Area
    categories: dict[str, Category]
    crawl: CrawlConfig
    sodra: SodraConfig = SodraConfig()


def load_settings() -> Settings:
    return Settings()


def settings_error_text(exc: ValidationError) -> str:
    """Failing field names only; never the input values (they may hold secrets)."""
    parts = []
    for err in exc.errors(include_input=False, include_url=False, include_context=False):
        name = ".".join(str(x) for x in err["loc"]).upper() or "SETTINGS"
        parts.append(f"{'missing' if err['type'] == 'missing' else 'invalid'} setting: {name}")
    return "; ".join(dict.fromkeys(parts)) or "invalid settings"


def load_settings_or_exit() -> Settings:
    """load_settings() for CLI entries: on a validation error print field names, exit 2."""
    try:
        return load_settings()
    except ValidationError as exc:
        print(settings_error_text(exc), file=sys.stderr)
        raise SystemExit(2) from None


def load_config(path: Path) -> AppConfig:
    return AppConfig.model_validate(yaml.safe_load(Path(path).read_text()))


def _mask(value: SecretStr | None) -> str | None:
    if value is None:
        return None
    return value.get_secret_value()[:4] + "…"


def main() -> None:
    s = load_settings_or_exit()
    settings = s.model_dump(mode="python")
    for k, v in settings.items():
        if isinstance(v, SecretStr):
            settings[k] = _mask(v)
    cfg = load_config(s.config_path)
    print(json.dumps({"settings": settings, "config": cfg.model_dump()},
                     default=str, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
