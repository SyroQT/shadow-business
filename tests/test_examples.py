"""The committed example output must be what `demo` makes from the committed snapshot."""
from pathlib import Path

from src.demo import newest_snapshot, run_demo

ROOT = Path(__file__).resolve().parent.parent


def test_demo_reproduces_example(tmp_path):
    out = run_demo(newest_snapshot(ROOT / "snapshots"), tmp_path / "demo.duckdb", tmp_path, 10)
    for name in ("leads.csv", "leads.md"):
        expected = ROOT / "examples" / out.name / name
        assert out.joinpath(name).read_bytes() == expected.read_bytes(), \
            f"{expected} is stale; regenerate with: python -m src.cli demo --out examples"
