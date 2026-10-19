"""Replay recorded API calls (tests/fixtures/recorded/) through httpx.MockTransport."""

import hashlib
import json
from pathlib import Path

import httpx

RECORDED = Path(__file__).parent / "fixtures" / "recorded"


def request_hash(method: str, url: str, body: dict | None) -> str:
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(f"{method.upper()}\n{url}\n{canonical}".encode()).hexdigest()


def load_recordings(*sources: str) -> dict[str, dict]:
    """request_hash -> record, for the given sources (default: all)."""
    dirs = [RECORDED / s for s in sources] if sources else [d for d in RECORDED.iterdir() if d.is_dir()]
    out = {}
    for d in dirs:
        for f in sorted(d.glob("*.json")):
            rec = json.loads(f.read_text())
            out[rec["request_hash"]] = rec
    return out


def replay_transport(*sources: str, calls: list | None = None) -> httpx.MockTransport:
    """MockTransport answering recorded requests; unknown requests fail loudly.
    Pass a list as `calls` to collect the hashes of requests served."""
    recordings = load_recordings(*sources)

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        h = request_hash(request.method, str(request.url), body)
        rec = recordings.get(h)
        if rec is None:
            raise AssertionError(f"no recording for {request.method} {request.url} ({h[:12]})")
        if calls is not None:
            calls.append(h)
        resp = rec["response"]
        if "json" in resp:
            return httpx.Response(resp["status"], json=resp["json"])
        return httpx.Response(resp["status"], text=resp["text"], headers=resp["headers"])

    return httpx.MockTransport(handler)
