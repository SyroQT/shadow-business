"""Extraction data-quality report, built only from SQL over raw.* for one run_id."""
import re
from pathlib import Path

import duckdb

from src.extract.places import note_int

TITLE = "Extraction data-quality report"


def _pct(num: int, den: int) -> str:
    return f"{100 * num / den:.1f}%" if den else "n/a"


def _dur(seconds: float | None) -> str:
    if seconds is None:
        return "n/a"
    s = int(round(seconds))
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h}h {m}m {sec}s" if h else f"{m}m {sec}s"


def _table(header: list[str], rows: list[list]) -> list[str]:
    out = ["| " + " | ".join(header) + " |", "|" + "|".join("---" for _ in header) + "|"]
    out += ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
    return out


def latest_run_id(con: duckdb.DuckDBPyConnection, run_month: str) -> str | None:
    row = con.execute(
        "select run_id from raw.run_log where run_month = ?"
        " order by finished_at desc, started_at desc limit 1", [run_month]).fetchone()
    return row[0] if row else None


def _run_month(con, run_id: str) -> str:
    row = con.execute("select run_month from raw.run_log where run_id = ? limit 1",
                      [run_id]).fetchone()
    return row[0] if row else run_id.split("_")[0]


def _places_section(con, run_id: str, places_notes: str | None) -> list[str]:
    cats = con.execute(
        "select category, count(*), count(distinct place_id) from raw.places"
        " where run_id = ? group by 1 order by 1", [run_id]).fetchall()
    total, web = con.execute(
        "select count(distinct place_id),"
        " count(distinct place_id) filter (where payload->>'websiteUri' is not null)"
        " from raw.places where run_id = ?", [run_id]).fetchone()
    crawled = con.execute(
        "select count(distinct place_id) from raw.web_pages where run_id = ?"
        " and page_role = 'home' and skip_reason is null"
        " and place_id in (select place_id from raw.places where run_id = ?)",
        [run_id, run_id]).fetchone()[0]
    out = ["## Places per category", ""]
    out += _table(["category", "rows", "unique"], [list(r) for r in cats])
    out += ["", f"Distinct places: {total}", "",
            f"Website share: {_pct(web, total)} ({web} of {total})", "",
            f"Crawled share: {_pct(crawled, total)} ({crawled} of {total})"]
    skips = con.execute(
        "select coalesce(skip_reason, '(crawled)'), count(*) from raw.web_pages"
        " where run_id = ? and page_role = 'home' group by skip_reason order by 2 desc, 1",
        [run_id]).fetchall()
    out += ["", "## Skip reasons (home pages)", ""]
    out += _table(["skip_reason", "pages"], [list(r) for r in skips]) if skips else ["None."]
    names = sorted({c for c, _, _ in cats}
                   | set(re.findall(r"(?:^|; )([^:;]+):(?:saturated_tiles|coverage_gaps)=",
                                    places_notes or "")))
    out += ["", "## Saturated tiles and coverage gaps", ""]
    out += (_table(["category", "saturated_tiles", "coverage_gaps"],
                   [[c, note_int(places_notes, c, "saturated_tiles"),
                     note_int(places_notes, c, "coverage_gaps")] for c in names])
            if names else ["None."])
    return out


def _sodra_section(con, run_id: str) -> list[str]:
    out = ["## Sodra", ""]
    cutoff = con.execute("select max(finished_at) from raw.run_log where run_id = ?",
                         [run_id]).fetchone()[0]
    runs = [r[0] for r in con.execute(
        "select distinct run_id from raw.run_log where step = 'sodra' and finished_at <= ?",
        [cutoff]).fetchall()] if cutoff is not None else []
    sha = None
    if runs:
        marks = ",".join("?" * len(runs))
        row = con.execute(
            f"select file_sha256 from raw.sodra where run_id in ({marks})"
            " order by retrieved_at desc limit 1", runs).fetchone()
        sha = row[0] if row else None
    if sha is None:
        return out + ["Sodra: no data landed"]
    marks = ",".join("?" * len(runs))
    months = con.execute(
        f"select data_month, count(*), count(*) filter (where trim(coalesce(\"jarCode\", '')) <> '')"
        f" from raw.sodra where file_sha256 = ? and run_id in ({marks}) group by 1 order by 1",
        [sha, *runs]).fetchall()
    n = sum(m[1] for m in months)
    jar = sum(m[2] for m in months)
    out += [f"Sodra rows: {n}", "", f"jarCode share: {_pct(jar, n)}", ""]
    out += _table(["data_month", "rows", "jarCode share"],
                  [[m, c, _pct(j, c)] for m, c, j in months])
    return out


def _note(notes: str | None, key: str) -> int | None:
    m = re.search(rf"(?:^|; ){re.escape(key)}=(\d+)", notes or "")
    return int(m.group(1)) if m else None


def _cell(notes: str | None) -> str:
    """Notes truncated to 120 characters, then `|` escaped so the cut never splits an escape."""
    text = (notes or "").replace("\n", " ")
    if len(text) > 120:
        text = text[:117] + "..."
    return text.replace("|", "\\|")


def _steps_section(con, run_id: str) -> list[str]:
    rows = con.execute(
        "select step, status, api_requests, cache_hits,"
        " date_diff('millisecond', started_at, finished_at) / 1000.0, notes from raw.run_log"
        " where run_id = ? order by started_at, step", [run_id]).fetchall()
    total = con.execute(
        "select date_diff('millisecond', min(started_at), max(finished_at)) / 1000.0"
        " from raw.run_log where run_id = ?", [run_id]).fetchone()[0]
    table = []
    for s, st, a, c, d, n in rows:
        table.append([s, st, a, c, _dur(d), _cell(n)])
        if s == "crawl" and _note(n, "robots_requests") is not None:
            table.append(["robots", "-", _note(n, "robots_requests"),
                          _note(n, "robots_cache_hits") or 0, "-", ""])
    out = ["## Steps", ""]
    out += _table(["step", "status", "api_requests", "cache_hits", "duration", "notes"], table)
    return out + ["", f"Total duration: {_dur(total)}"]


def build_report(con: duckdb.DuckDBPyConnection, run_id: str) -> str:
    notes = {step: n for step, n in con.execute(
        "select step, notes from raw.run_log where run_id = ? order by finished_at",
        [run_id]).fetchall()}
    lines = [f"# {TITLE}", "", f"Run: {run_id}", ""]
    lines += _places_section(con, run_id, notes.get("places")) + [""]
    lines += _sodra_section(con, run_id) + [""]
    lines += _steps_section(con, run_id)
    return "\n".join(lines) + "\n"


def write_report(con: duckdb.DuckDBPyConnection, run_id: str,
                 out_root: Path = Path("output"), dir_name: str | None = None) -> Path:
    path = Path(out_root) / (dir_name or _run_month(con, run_id)) / "extract_report.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(build_report(con, run_id), encoding="utf-8")
    return path
