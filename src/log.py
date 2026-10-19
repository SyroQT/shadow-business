"""JSON-lines logging to stdout."""
import json
from datetime import UTC, datetime


def log(event: str, **fields) -> None:
    record = {"ts": datetime.now(UTC).isoformat(), "event": event, **fields}
    print(json.dumps(record, default=str), flush=True)
