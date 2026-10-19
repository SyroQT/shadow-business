"""Replay recorded API calls (tests/fixtures/recorded/) through httpx.MockTransport."""

import json
from collections.abc import Callable
from pathlib import Path

import httpx

from src.cache import request_hash

RECORDED = Path(__file__).parent / "fixtures" / "recorded"


def load_recordings(*sources: str) -> dict[str, dict]:
    """request_hash -> record, for the given sources (default: all)."""
    dirs = [RECORDED / s for s in sources] if sources else [d for d in RECORDED.iterdir() if d.is_dir()]
    out = {}
    for d in dirs:
        for f in sorted(d.glob("*.json")):
            rec = json.loads(f.read_text())
            out[rec["request_hash"]] = rec
    return out


def replay_transport(*sources: str, calls: list | None = None,
                     fallback: Callable[[httpx.Request], httpx.Response] | None = None
                     ) -> httpx.MockTransport:
    """MockTransport answering recorded requests; unknown requests fail loudly unless
    a `fallback` handler is given. Pass a list as `calls` to collect served hashes."""
    recordings = load_recordings(*sources)

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        h = request_hash(request.method, str(request.url), body)
        rec = recordings.get(h)
        if rec is None:
            if fallback is not None:
                return fallback(request)
            raise AssertionError(f"no recording for {request.method} {request.url} ({h[:12]})")
        if calls is not None:
            calls.append(h)
        resp = rec["response"]
        if "json" in resp:
            return httpx.Response(resp["status"], json=resp["json"])
        return httpx.Response(resp["status"], text=resp["text"], headers=resp["headers"])

    return httpx.MockTransport(handler)
