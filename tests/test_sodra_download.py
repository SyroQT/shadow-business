import hashlib
import io
import zipfile
from pathlib import Path

import httpx
import pytest

from src.cache import ApiError, sodra_client
from src.db import insert_rows
from src.extract.sodra import SodraBlocked, acquire, already_landed
from src.run import new_run
from src.settings import Settings

URL = "https://atvira.example.test/monthly-2026.csv.zip"


def make_zip() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("a.csv", "x;y\n1;2\n")
    return buf.getvalue()


def settings(**kw) -> Settings:
    return Settings(google_api_key="g", firecrawl_api_key="f", _env_file=None, **kw)


def client_for(handler, settings_):
    return sodra_client(settings_, transport=httpx.MockTransport(handler), sleep=lambda s: None)


def test_local_file_copied_by_sha(tmp_path):
    src = tmp_path / "in" / "m.zip"
    src.parent.mkdir()
    data = make_zip()
    src.write_bytes(data)
    dest = tmp_path / "dest"
    path, url, sha = acquire(settings(sodra_file=src), None, dest)
    assert sha == hashlib.sha256(data).hexdigest()
    assert path == dest / f"{sha}.zip" and path.read_bytes() == data
    assert src.exists()
    assert url == src.resolve().as_uri() and url.startswith("file://")
    # second call: existing copy is kept
    assert acquire(settings(sodra_file=src), None, dest)[0] == path


def test_local_file_not_zip(tmp_path):
    src = tmp_path / "m.zip"
    src.write_bytes(b"hello")
    with pytest.raises(ApiError):
        acquire(settings(sodra_file=src), None, tmp_path / "d")


def test_url_download_and_sha(tmp_path):
    data = make_zip()
    s = settings(sodra_url=URL)
    path, url, sha = acquire(s, client_for(lambda r: httpx.Response(200, content=data), s), tmp_path)
    assert url == URL and sha == hashlib.sha256(data).hexdigest()
    assert path == tmp_path / f"{sha}.zip" and path.read_bytes() == data
    assert list(tmp_path.glob("*.tmp")) == []


def test_cloudflare_challenge_raises_blocked(tmp_path):
    s = settings(sodra_url=URL)
    c = client_for(lambda r: httpx.Response(403, text="<html>Just a moment...</html>"), s)
    with pytest.raises(SodraBlocked, match="SODRA_FILE"):
        acquire(s, c, tmp_path)
    c = client_for(lambda r: httpx.Response(403, headers={"cf-mitigated": "challenge"}, text="x"), s)
    with pytest.raises(SodraBlocked):
        acquire(s, c, tmp_path)


def test_plain_403_still_auth_error(tmp_path):
    from src.cache import AuthError

    s = settings(sodra_url=URL)
    with pytest.raises(AuthError):
        acquire(s, client_for(lambda r: httpx.Response(403, text="nope"), s), tmp_path)


def test_non_zip_body_rejected(tmp_path):
    s = settings(sodra_url=URL)
    with pytest.raises(ApiError):
        acquire(s, client_for(lambda r: httpx.Response(200, text="<html>hi</html>"), s), tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_neither_setting(tmp_path):
    with pytest.raises(ValueError, match="SODRA_URL or SODRA_FILE"):
        acquire(settings(), None, tmp_path)


def test_already_landed_empty_then_true_after_insert(con):
    assert already_landed(con, "abc") is False
    insert_rows(con, "sodra", [{"file_sha256": "abc"}], new_run("2026-10"))
    assert already_landed(con, "abc") is True
    assert already_landed(con, "other") is False
