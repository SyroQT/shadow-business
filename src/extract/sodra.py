"""Monthly Sodra zip -> raw_sodra."""
import hashlib
import shutil
from pathlib import Path

import duckdb

from src.cache import ApiError, AuthError, SourceClient
from src.settings import Settings

ZIP_MAGIC = b"PK\x03\x04"
BLOCKED_MESSAGE = (
    "Sodra is behind a Cloudflare challenge and refused the scripted download. "
    "Download the monthly zip in a browser and set SODRA_FILE to its path."
)


class SodraBlocked(Exception):
    """Cloudflare challenge blocked the download."""


def _is_challenge(e: AuthError) -> bool:
    headers = {k.lower() for k in e.headers}
    return "cf-mitigated" in headers or "Just a moment" in e.body


def _check_zip(head: bytes, where: str) -> None:
    if not head.startswith(ZIP_MAGIC):
        raise ApiError(None, f"{where} is not a zip file")


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def acquire(settings: Settings, client: SourceClient | None,
            dest_dir: Path = Path("data/raw/sodra")) -> tuple[Path, str, str]:
    """Land the monthly zip as {dest_dir}/{sha256}.zip; return (path, source_url, sha256)."""
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)

    if settings.sodra_file is not None:
        src = Path(settings.sodra_file)
        with src.open("rb") as f:
            _check_zip(f.read(4), str(src))
        sha = _sha256_file(src)
        dest = dest_dir / f"{sha}.zip"
        if not dest.exists():
            tmp = dest.with_suffix(".tmp")
            shutil.copyfile(src, tmp)
            tmp.replace(dest)
        return dest, settings.sodra_url or src.resolve().as_uri(), sha

    if settings.sodra_url:
        if client is None:
            raise ValueError("a SourceClient is required to download SODRA_URL")
        try:
            resp = client.request("GET", settings.sodra_url, use_cache=False)
        except AuthError as e:
            if _is_challenge(e):
                raise SodraBlocked(BLOCKED_MESSAGE) from None
            raise
        _check_zip(resp.content, settings.sodra_url)
        sha = hashlib.sha256(resp.content).hexdigest()
        dest = dest_dir / f"{sha}.zip"
        if not dest.exists():
            tmp = dest.with_suffix(".tmp")
            tmp.write_bytes(resp.content)
            tmp.replace(dest)
        return dest, settings.sodra_url, sha

    raise ValueError("set SODRA_URL or SODRA_FILE")


def already_landed(con: duckdb.DuckDBPyConnection, sha: str) -> bool:
    return con.execute("SELECT 1 FROM raw.sodra WHERE file_sha256=? LIMIT 1", [sha]).fetchone() is not None
