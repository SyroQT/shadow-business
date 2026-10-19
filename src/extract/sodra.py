"""Monthly Sodra zip -> raw_sodra."""
import csv
import hashlib
import re
import shutil
import sys
import tempfile
import zipfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import duckdb

from src.cache import ApiError, AuthError, SourceClient, sodra_client
from src.db import StepStats, connect, step
from src.log import log
from src.run import RunContext, new_run
from src.settings import Settings, load_settings_or_exit

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


EXPECTED_COLUMNS = [
    "code", "jarCode", "name", "municipality", "ecoActCode", "ecoActCodeStr",
    "ecoActName", "month", "avgWage", "numInsured", "avgWage2", "numInsured2", "tax",
]
_TOKEN_RE = re.compile(r"\(([A-Za-z0-9]+)\)\s*$")
_MONTH_RE = re.compile(r"^\d{6}$")


def map_headers(headers: list[str]) -> list[str]:
    out, bad = [], []
    for h in headers:
        m = _TOKEN_RE.search(h.strip())
        if m:
            out.append(m.group(1))
        else:
            bad.append(h)
    if bad:
        raise ValueError(f"headers without a (token): {bad}")
    return out


def _decode_prefix(sample: bytes, encoding: str) -> str:
    # a sample cut mid-character must not look like a decode error
    for cut in range(0, 4):
        try:
            return (sample[: len(sample) - cut] if cut else sample).decode(encoding)
        except UnicodeDecodeError as e:
            if cut == 3 or e.start < len(sample) - 3:
                raise
    raise AssertionError("unreachable")


def sniff(sample: bytes) -> tuple[str, str]:
    """Return (encoding, delimiter) for the head of a CSV file."""
    encoding = "utf-8-sig" if sample.startswith(b"\xef\xbb\xbf") else "utf-8"
    try:
        text = _decode_prefix(sample, encoding)
    except UnicodeDecodeError:
        encoding = "cp1257"
        text = sample.decode(encoding)
    first_line = text.splitlines()[0] if text else ""
    # csv.Sniffer alone picks "," for the real header (it contains a comma inside a
    # field name), so take the most frequent candidate and use Sniffer only to break ties.
    counts = {d: first_line.count(d) for d in ";\t,"}
    best = max(counts.values())
    leaders = [d for d, n in counts.items() if n == best]
    if best > 0 and len(leaders) == 1:
        delim = leaders[0]
    else:
        delim = csv.Sniffer().sniff(first_line, delimiters=";\t,").delimiter
    return encoding, delim


@dataclass
class ValidationReport:
    rows: int = 0
    months: dict[str, int] = field(default_factory=dict)
    missing_columns: list[str] = field(default_factory=list)
    jar9_share: float = 1.0
    bad_num_insured: int = 0
    failures: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.failures


def _month_index(m: str) -> int:
    return int(m[:4]) * 12 + int(m[4:]) - 1


def validate(con: duckdb.DuckDBPyConnection, staging: str,
             min_rows_per_month: int = 50_000) -> ValidationReport:
    r = ValidationReport()
    cols = {row[0] for row in con.execute(f"DESCRIBE {staging}").fetchall()}
    r.missing_columns = [c for c in EXPECTED_COLUMNS if c not in cols]
    if r.missing_columns:
        r.failures.append(f"missing columns: {r.missing_columns}")
        return r

    r.rows = con.execute(f"SELECT count(*) FROM {staging}").fetchone()[0]
    r.months = {
        (m if m is not None else ""): n
        for m, n in con.execute(
            f'SELECT "month", count(*) FROM {staging} GROUP BY 1 ORDER BY 1').fetchall()
    }
    bad_fmt = [m for m in r.months if not _MONTH_RE.match(m) or not 1 <= int(m[4:]) <= 12]
    if bad_fmt:
        r.failures.append(f"month values not yyyymm: {bad_fmt[:5]}")
    elif r.months:
        idx = sorted(_month_index(m) for m in r.months)
        first = min(r.months)
        if first[4:] != "01":
            r.failures.append(f"months must start at {first[:4]}01, first is {first}")
        if idx != list(range(idx[0], idx[0] + len(idx))):
            r.failures.append(f"months are not contiguous: {sorted(r.months)}")
        for m, n in sorted(r.months.items()):
            if n < min_rows_per_month:
                r.failures.append(f"month {m} has {n} rows, need >= {min_rows_per_month}")
    else:
        r.failures.append("no rows")

    nonempty, nine = con.execute(
        f"""SELECT count(*), count(*) FILTER (WHERE regexp_matches("jarCode", '^[0-9]{{9}}$'))
            FROM {staging} WHERE "jarCode" IS NOT NULL AND "jarCode" <> ''""").fetchone()
    r.jar9_share = nine / nonempty if nonempty else 1.0
    if r.jar9_share < 0.99:
        r.failures.append(f"jarCode 9-digit share {r.jar9_share:.4f} < 0.99")

    r.bad_num_insured = con.execute(
        f"""SELECT count(*) FROM {staging} WHERE "numInsured" IS NOT NULL
            AND "numInsured" <> '' AND TRY_CAST("numInsured" AS BIGINT) IS NULL""").fetchone()[0]
    if r.bad_num_insured:
        r.failures.append(f"{r.bad_num_insured} non-integer numInsured values")
    return r


def _extract_csv(zip_path: Path, out_dir: Path) -> Path:
    with zipfile.ZipFile(zip_path) as z:
        members = [i for i in z.infolist() if not i.is_dir() and i.filename.lower().endswith(".csv")]
        if len(members) != 1:
            raise ValueError(f"expected exactly one .csv in {zip_path.name}, found {len(members)}")
        dest = out_dir / "sodra.csv"
        with z.open(members[0]) as src, dest.open("wb") as dst:
            shutil.copyfileobj(src, dst, 1 << 20)
    return dest


def _load_staging(con: duckdb.DuckDBPyConnection, csv_path: Path, work: Path) -> None:
    with csv_path.open("rb") as f:
        head = f.read(65536)
    encoding, delim = sniff(head)
    if encoding == "cp1257":
        converted = work / "sodra.utf8.csv"
        with csv_path.open("r", encoding="cp1257", newline="") as src, \
                converted.open("w", encoding="utf-8", newline="") as dst:
            shutil.copyfileobj(src, dst, 1 << 20)
        csv_path = converted
        encoding = "utf-8"
    with csv_path.open("r", encoding=encoding, newline="") as f:
        header = next(csv.reader(f, delimiter=delim, quotechar='"'))
    names = map_headers(header)
    con.execute("DROP TABLE IF EXISTS sodra_staging")
    con.execute(
        "CREATE TEMP TABLE sodra_staging AS SELECT * FROM read_csv(?, delim=?, quote='\"', "
        "header=true, all_varchar=true, names=?)",
        [str(csv_path), delim, names],
    )


def run_sodra(con: duckdb.DuckDBPyConnection, settings: Settings, client: SourceClient | None,
              run: RunContext, min_rows_per_month: int = 50_000,
              dest_dir: Path = Path("data/raw/sodra")) -> StepStats:
    with step(con, run, "sodra") as stats:
        try:
            zip_path, source_url, sha = acquire(settings, client, dest_dir)
            if already_landed(con, sha):
                stats.notes.append(f"duplicate_sha={sha[:12]}")
                stats.items_ok = 0
                return stats
            with tempfile.TemporaryDirectory(prefix="sodra_") as tmp:
                csv_path = _extract_csv(zip_path, Path(tmp))
                _load_staging(con, csv_path, Path(tmp))
            report = validate(con, "sodra_staging", min_rows_per_month)
            log("sodra_validation", step="sodra", run_id=run.run_id, rows=report.rows,
                months=dict(sorted(report.months.items())), jar9_share=report.jar9_share,
                bad_num_insured=report.bad_num_insured, failures=report.failures)
            if not report.ok:
                raise ValueError("; ".join(report.failures))
            cols = ", ".join(f'"{c}"' for c in EXPECTED_COLUMNS)
            retrieved_at = datetime.now(UTC).replace(tzinfo=None)
            con.execute("BEGIN")
            try:
                con.execute(
                    f'INSERT INTO raw.sodra SELECT ?, ?, ?, ?, "month" AS data_month, {cols} '
                    "FROM sodra_staging",
                    [run.run_id, retrieved_at, source_url, sha],
                )
                con.execute("COMMIT")
            except BaseException:
                con.execute("ROLLBACK")
                raise
            stats.items_ok = report.rows
        finally:
            con.execute("DROP TABLE IF EXISTS sodra_staging")
            if client is not None:
                stats.api_requests = client.api_requests
                stats.cache_hits = client.cache_hits
    return stats


def main() -> int:
    settings = load_settings_or_exit()
    run = new_run(settings.run_month)
    con = connect(settings.db_path)
    try:
        client = sodra_client(settings)
        stats = run_sodra(con, settings, client, run)
    except Exception as e:
        log("sodra_failed", error=f"{type(e).__name__}: {e}")
        return 1
    finally:
        con.close()
    log("sodra_done", items_ok=stats.items_ok, notes="; ".join(stats.notes))
    return 0


if __name__ == "__main__":
    sys.exit(main())
