#!/usr/bin/env python3
"""Build the weekly BTC options OI-level report (reports/weekly/*.md) from
the archived snapshots.

This is a written pre-weekly/recap commentary next to the hourly tracker
dashboard (scripts/build_dashboard.py / docs/index.html), which now also
renders the same Call-/Put-OI-level chart and table directly in
docs/index.html. This script only writes the Markdown report; it reads
call_put_levels_for_snapshot/build_call_put_series/build_segments/
check_level_hit/build_levels from build_dashboard.py rather than
duplicating that logic. No network access and no third-party dependencies.
"""
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_dashboard import (  # noqa: E402
    build_call_put_series,
    build_levels,
    build_segments,
    load_all_snapshots,
)

REPORT_DIR = REPO_ROOT / "reports" / "weekly"


def fmt_num(v):
    if v is None:
        return "–"
    return f"{v:,.0f}"


def fmt_ts(ts):
    if not ts:
        return "–"
    return ts.replace("T", " ").replace("Z", " UTC")


def level_status_text(lvl):
    status = lvl.get("status")
    if status == "hit":
        return f"getroffen am {fmt_ts(lvl['hit_ts'])}"
    if status == "not_hit":
        return "nicht getroffen (letzte 10 Tage)"
    return "keine Preisdaten"


def report_type_for_weekday(iso_weekday):
    if iso_weekday == 1:
        return "pre-weekly", "Pre-Weekly"
    if iso_weekday == 6:
        return "recap", "Weekly Recap"
    return "snapshot", "Snapshot"


def render_markdown(label, date_str, now, current_price, levels, has_candles):
    lines = [f"# BTC Weekly OI Report — {label} — {date_str}", ""]
    lines.append(f"**Erzeugt:** {now.strftime('%Y-%m-%d %H:%M:%S')} UTC")
    lines.append("")
    if current_price is not None:
        lines.append(f"**Aktueller BTC Index Preis:** ${fmt_num(current_price)}")
    else:
        lines.append("**Aktueller BTC Index Preis:** – (kein Preis-Datenpunkt im neuesten Snapshot)")
    lines.append("")

    if not has_candles:
        lines.append(
            "> Hinweis: Noch keine H1-Preisdaten verfuegbar, erscheinen ab dem naechsten Fetch-Lauf."
        )
        lines.append("")

    if not levels:
        lines.append("Noch keine Top-Strike-Level erfasst (zu wenige Snapshots).")
        lines.append("")
        return "\n".join(lines) + "\n"

    lines.append("## Top-Strike-Level nach Open Interest")
    lines.append("")
    lines.append("| Richtung | Strike | Zuerst beobachtet am | Status |")
    lines.append("|---|---|---|---|")
    for lvl in levels:
        lines.append(
            f"| {lvl['direction']} | {fmt_num(lvl['strike'])} | {fmt_ts(lvl['first_seen_timestamp_utc'])} | {level_status_text(lvl)} |"
        )
    lines.append("")

    total = len(levels)
    hit_count = sum(1 for l in levels if l.get("status") == "hit")
    if has_candles:
        lines.append(
            f"{hit_count} von {total} beobachteten Level(s) wurden in den letzten 10 Tagen erreicht."
        )
    else:
        lines.append(f"{total} Level beobachtet — Treffer-Status folgt, sobald H1-Preisdaten vorliegen.")
    lines.append("")
    return "\n".join(lines) + "\n"


def main():
    snapshots = load_all_snapshots()
    if not snapshots:
        raise SystemExit("Keine Snapshots unter snapshots/*-UTC.json gefunden — Weekly Report nicht erzeugt.")

    call_series, put_series = build_call_put_series(snapshots)
    call_segments = build_segments(call_series)
    put_segments = build_segments(put_series)

    latest_snapshot = snapshots[-1]
    current_price = latest_snapshot.get("btc_index_price")
    candles = latest_snapshot.get("btc_h1_candles") or []

    levels = build_levels(call_segments, put_segments, candles)

    now = datetime.now(timezone.utc)
    slug, label = report_type_for_weekday(now.isoweekday())
    date_str = now.strftime("%Y-%m-%d")

    md = render_markdown(label, date_str, now, current_price, levels, bool(candles))
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    report_path = REPORT_DIR / f"{date_str}-{slug}.md"
    report_path.write_text(md)

    print(
        f"OK: {report_path} erzeugt "
        f"({len(levels)} Level, H1-Daten: {'ja' if candles else 'nein'}, Typ: {label})"
    )


if __name__ == "__main__":
    main()
