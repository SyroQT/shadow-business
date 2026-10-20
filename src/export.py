"""Export the latest run's leads: output/<run_month>/leads.csv and leads.md."""
import argparse
import csv
import sys
from pathlib import Path

import duckdb

from src.log import log
from src.settings import load_settings_or_exit

TIERS = ("flag", "watch", "clear", "excluded")
CARD_WIDTH = 78
NO_AVG_WAGE = "average wage not published"
NO_COMPARISON = "no comparison available"
NO_CONTEXT = "(no text snippet captured)"
NO_CAVEAT = "none"


def _cell(value) -> str:
    """One CSV cell: None -> empty, lists joined with '; ', places as 'name (uri)'."""
    if value is None:
        return ""
    if isinstance(value, list):
        return "; ".join(_place(v) if isinstance(v, dict) else str(v) for v in value)
    return str(value)


def _place(p: dict) -> str:
    return f"{p['name']} ({p['google_maps_uri']})" if p.get("google_maps_uri") else str(p["name"])


def _money(value) -> str:
    return f"€{float(value):.2f}"


def _plain_money(value) -> str:
    v = float(value)
    return str(int(v)) if v == int(v) else f"{v:.2f}"


def _mult(value, digits: int = 1) -> str:
    return f"{float(value):.{digits}f}×"


def _card(r: dict, label: str) -> str:
    head = f"{r['tier'].upper()} #{r['lead_rank']}  {r['company_name']} ({label})"
    right = f"run {r['run_month']}"
    lines = [head.ljust(max(CARD_WIDTH - len(right), len(head) + 2)) + right,
             f"  Reason: {r['lead_reason']}"]
    comparison = (f"{r['reviews_per_employee']:.1f} reviews per employee, "
                  f"{_mult(r['rpe_vs_median'])} the {label} median ({r['category_median_rpe']:.1f})"
                  if r["rpe_vs_median"] is not None else NO_COMPARISON)
    lines += [
        f"  Busy:   {r['chain_user_rating_count']} Google reviews, {r['n_places']} place(s); "
        f"{r['num_insured']} insured employees",
        f"          → {comparison}",
    ]
    if r["avg_wage"] is None:
        paid = NO_AVG_WAGE
    else:
        ratio = f", {_mult(r['wage_vs_minimum'], 2)} the minimum wage" \
            if r["wage_vs_minimum"] is not None else ""
        paid = f"average wage {_money(r['avg_wage'])}{ratio} (€{_plain_money(r['min_monthly_wage'])})"
    context = " ".join(r["code_context"].split()) if r["code_context"] else None
    lines += [
        f"  Paid:   {paid}",
        f"  Match:  company code {r['jar_code']} on {r['code_source_url']}",
        f'          "{context}"' if context else f"          {NO_CONTEXT}",
        f"          Sodra: {r['company_name']}, EVRK {r['evrk_code']}, "
        f"data month {str(r['sodra_month'])[:7]}",
    ]
    lines += [f"  Places: {p['name']} — {p['google_maps_uri']}" if p.get("google_maps_uri")
              else f"  Places: {p['name']}" for p in r["places"]]
    lines += [f"  Note:   {r['category_caveat'] or NO_CAVEAT}",
              f"  Last month: {r['prev_tier'] or 'not present'}"]
    return "```text\n" + "\n".join(lines) + "\n```\n"


def _summary(counts: dict[str, dict[str, int]]) -> str:
    out = ["| category | " + " | ".join(TIERS) + " |",
           "|---|" + "---:|" * len(TIERS)]
    for label in sorted(counts):
        out.append(f"| {label} | " + " | ".join(str(counts[label].get(t, 0)) for t in TIERS) + " |")
    return "\n".join(out) + "\n"


def _thresholds(data: list[dict]) -> str:
    r = data[0]
    return (f"Thresholds: busy ≥ {float(r['busy_multiple']):.1f}× the category median; "
            f"low wage = average wage near the minimum wage "
            f"(€{_plain_money(r['min_monthly_wage'])}).\n")


def export_leads(db_path: Path, out_dir: Path, top_n: int) -> Path | None:
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        latest = con.execute("select max(run_month) from intermediate.int_places_matched"
                             ).fetchone()[0]
        month = con.execute("select max(run_month) from marts.mart_leads").fetchone()[0]
        if month is None or month != latest:
            log("export", status="no_rows", run_month=latest)
            return None
        cur = con.execute("select * from marts.mart_leads where run_month = ? "
                          "order by priority_rank nulls last, exclusion_reason, jar_code",
                          [month])
        columns = [d[0] for d in cur.description]
        data = [dict(zip(columns, row, strict=True)) for row in cur.fetchall()]
        labels = dict(con.execute("select category, label from staging.category_lead_rules")
                      .fetchall())
    finally:
        con.close()

    target = Path(out_dir) / month
    target.mkdir(parents=True, exist_ok=True)
    with (target / "leads.csv").open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f, lineterminator="\n")
        writer.writerow(columns)
        for r in data:
            writer.writerow([_cell(r[c]) for c in columns])

    def label_of(r: dict) -> str:
        return labels.get(r["lead_category"], r["lead_category"] or "unknown category")

    counts: dict[str, dict[str, int]] = {}
    for r in data:
        by_tier = counts.setdefault(label_of(r), {})
        by_tier[r["tier"]] = by_tier.get(r["tier"], 0) + 1

    leads = [r for r in data if r["tier"] in ("flag", "watch")]
    leads.sort(key=lambda r: (r["tier"] != "flag", r["lead_rank"]))
    near = sorted((r for r in data if r["tier"] == "clear" and r["priority_rank"] is not None
                   and r["priority_rank"] <= top_n), key=lambda r: r["priority_rank"])

    parts = [f"# Leads — run {month}\n", _thresholds(data), _summary(counts)]
    if not leads:
        parts.append("No flag or watch leads this month.\n")
    parts += [_card(r, label_of(r)) for r in leads]
    if near:
        parts.append("## Closest to the threshold (not flagged)\n")
        parts += [_card(r, label_of(r)) for r in near]
    (target / "leads.md").write_text("\n".join(parts), encoding="utf-8")
    log("export", status="ok", run_month=month, rows=len(data),
        cards=len(leads) + len(near), path=str(target))
    return target


def main(argv: list[str] | None = None) -> int:
    settings = load_settings_or_exit()
    parser = argparse.ArgumentParser(prog="python -m src.export")
    parser.add_argument("--top", type=int, default=settings.leads_top_n)
    parser.add_argument("--out", type=Path, default=Path("output"))
    args = parser.parse_args(argv)
    try:
        result = export_leads(settings.db_path, args.out, args.top)
    except (duckdb.IOException, duckdb.CatalogException) as e:
        print(f"cannot export: {settings.db_path} has no usable marts.mart_leads "
              f"({str(e).splitlines()[0]})", file=sys.stderr)
        return 1
    if result is None:
        print("no leads for the latest run month; nothing exported", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
