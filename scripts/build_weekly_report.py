#!/usr/bin/env python3
"""Build the weekly BTC options OI-level report (reports/weekly/*.md) and
the standalone docs/weekly.html page from the archived snapshots.

This is a new, additive report type next to the existing 3x-daily tracker
(scripts/build_dashboard.py / docs/index.html) — it does not read or write
any of that tracker's files. For each archived snapshot it tracks two
separate levels: the strike with the highest call OI among strikes above
that snapshot's BTC index price ("Call-Level", upside potential /
resistance) and the strike with the highest put OI among strikes below
the price ("Put-Level", downside potential / support). This reflects the
market's implied volatility expectation. It then checks whether the
BTC-PERPETUAL price later traded through each level, using the hourly
price candles fetched by scripts/fetch_deribit.py (the btc_h1_candles
field). No network access and no third-party dependencies.
"""
import glob
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_dashboard import aggregate  # noqa: E402

SNAPSHOT_GLOB = str(REPO_ROOT / "snapshots" / "*-UTC.json")
WEEKLY_TEMPLATE_PATH = REPO_ROOT / "scripts" / "weekly_template.html"
REPORT_DIR = REPO_ROOT / "reports" / "weekly"
OUT_HTML_PATH = REPO_ROOT / "docs" / "weekly.html"
MAX_LEVELS_PER_SIDE = 2
HIT_TOLERANCE = 0.0025


def load_all_snapshots():
    files = sorted(glob.glob(SNAPSHOT_GLOB))
    snapshots = []
    for path in files:
        with open(path) as f:
            snapshots.append(json.load(f))
    snapshots.sort(key=lambda s: s["timestamp_utc"])
    return snapshots


def call_put_levels_for_snapshot(snapshot, agg):
    """Return (call_strike, put_strike) for one snapshot: the highest-call-OI
    strike strictly above the BTC index price, and the highest-put-OI strike
    strictly below it. Either side is None if no such strike has OI > 0."""
    price = snapshot.get("btc_index_price")
    if price is None:
        return None, None

    call_strike, call_oi = None, None
    put_strike, put_oi = None, None
    for strike, row in agg["by_strike"].items():
        if strike > price and row["call_oi"] > 0:
            if call_oi is None or row["call_oi"] > call_oi:
                call_strike, call_oi = strike, row["call_oi"]
        if strike < price and row["put_oi"] > 0:
            if put_oi is None or row["put_oi"] > put_oi:
                put_strike, put_oi = strike, row["put_oi"]
    return call_strike, put_strike


def build_call_put_series(snapshots):
    call_series = []
    put_series = []
    for snap in snapshots:
        agg = aggregate(snap)
        call_strike, put_strike = call_put_levels_for_snapshot(snap, agg)
        if call_strike is not None:
            call_series.append((snap["timestamp_utc"], call_strike))
        if put_strike is not None:
            put_series.append((snap["timestamp_utc"], put_strike))
    return call_series, put_series


def build_segments(series):
    segments = []
    for ts, strike in series:
        if segments and segments[-1]["strike"] == strike:
            continue
        segments.append({"strike": strike, "first_seen_timestamp_utc": ts})
    return segments


def check_level_hit(strike, candles):
    if not candles:
        return {"status": "no_data"}
    closes = [c["close"] for c in candles]
    lo, hi = min(closes), max(closes)
    if not (lo <= strike <= hi):
        return {"status": "not_hit"}
    for c in candles:
        if abs(c["close"] - strike) / strike <= HIT_TOLERANCE:
            return {"status": "hit", "hit_ts": c["ts_utc"]}
    return {"status": "not_hit"}


def report_type_for_weekday(iso_weekday):
    if iso_weekday == 1:
        return "pre-weekly", "Pre-Weekly"
    if iso_weekday == 6:
        return "recap", "Weekly Recap"
    return "snapshot", "Snapshot"


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


def render_kpi_block(current_price, levels):
    price_val = f"${fmt_num(current_price)}" if current_price is not None else "–"
    kpis = [
        ("Aktueller BTC-Preis", price_val),
        ("Beobachtete OI-Level", str(len(levels))),
    ]
    return "".join(
        f'<div class="kpi"><div class="kpi-label">{label}</div><div class="kpi-value">{value}</div></div>'
        for label, value in kpis
    )


def render_table_block(levels):
    if not levels:
        return '<div class="empty-note">Noch keine Top-Strike-Level erfasst (zu wenige Snapshots).</div>'
    rows = []
    for lvl in levels:
        status = lvl.get("status")
        if status == "hit":
            chip = f'<span class="status-chip hit">getroffen am {fmt_ts(lvl["hit_ts"])}</span>'
        elif status == "not_hit":
            chip = '<span class="status-chip not-hit">nicht getroffen</span>'
        else:
            chip = '<span class="status-chip not-hit">keine Preisdaten</span>'
        direction_class = "call" if lvl["direction"] == "Call" else "put"
        rows.append(
            f'<tr><td><span class="direction-chip {direction_class}">{lvl["direction"]}</span></td>'
            f'<td class="num">{fmt_num(lvl["strike"])}</td>'
            f'<td>{fmt_ts(lvl["first_seen_timestamp_utc"])}</td>'
            f"<td>{chip}</td></tr>"
        )
    return (
        '<table class="levels"><thead><tr>'
        "<th>Richtung</th><th>Strike</th><th>Zuerst beobachtet</th><th>Status</th>"
        "</tr></thead><tbody>" + "".join(rows) + "</tbody></table>"
    )


def render_chart_block(candles, levels):
    if not candles:
        return (
            '<div class="empty-note">Noch keine H1-Preisdaten verfügbar, '
            "erscheinen ab dem nächsten Fetch-Lauf.</div>"
        )

    w = max(640, len(candles) * 4)
    h = 320
    pad_l, pad_r, pad_t, pad_b = 60, 80, 20, 36
    plot_w = w - pad_l - pad_r
    plot_h = h - pad_t - pad_b

    closes = [c["close"] for c in candles]
    values = list(closes) + [lvl["strike"] for lvl in levels]
    v_min, v_max = min(values), max(values)
    span = max(v_max - v_min, 1)
    padding = span * 0.06
    v_min -= padding
    v_max += padding
    span = v_max - v_min

    n = len(candles)

    def x(i):
        return pad_l + (0 if n == 1 else (i / (n - 1)) * plot_w)

    def y(v):
        return pad_t + (1 - (v - v_min) / span) * plot_h

    price_path = " ".join(
        f'{"M" if i == 0 else "L"}{x(i):.1f},{y(c["close"]):.1f}' for i, c in enumerate(candles)
    )

    n_yticks = 5
    y_ticks = "".join(
        f'<text class="chart-axis-label" x="{pad_l - 8:.1f}" y="{y(v_min + (i / (n_yticks - 1)) * span) + 4:.1f}" text-anchor="end">{fmt_num(v_min + (i / (n_yticks - 1)) * span)}</text>'
        for i in range(n_yticks)
    )

    n_xticks = min(6, n)
    x_ticks = ""
    if n_xticks > 1:
        x_ticks = "".join(
            (
                lambda idx: f'<text class="chart-axis-label" x="{x(idx):.1f}" y="{h - pad_b + 16:.1f}" text-anchor="middle">'
                f'{candles[idx]["ts_utc"][5:10]} {candles[idx]["ts_utc"][11:16]}</text>'
            )(round(i * (n - 1) / (n_xticks - 1)))
            for i in range(n_xticks)
        )

    level_lines = []
    legend_items = []
    for lvl in levels:
        strike = lvl["strike"]
        is_current = lvl["age"] == "current"
        color = "var(--call)" if lvl["direction"] == "Call" else "var(--put)"
        opacity = 1.0 if is_current else 0.35
        yy = y(strike)
        dash = "" if is_current else 'stroke-dasharray="5 4"'
        level_lines.append(
            f'<line x1="{pad_l:.1f}" y1="{yy:.1f}" x2="{w - pad_r:.1f}" y2="{yy:.1f}" '
            f'stroke="{color}" stroke-width="2" {dash} opacity="{opacity}" />'
            f'<text class="chart-level-label" x="{w - pad_r + 6:.1f}" y="{yy + 4:.1f}" fill="{color}" opacity="{opacity}">{fmt_num(strike)}</text>'
        )
        legend_label = f"{lvl['direction']}-Level" + ("" if is_current else " (vorheriges)")
        legend_items.append(
            f'<span><i class="chart-swatch" style="background:{color};opacity:{opacity}"></i>{legend_label} {fmt_num(strike)}</span>'
        )

    return f"""
    <div class="chart-legend">{"".join(legend_items)}</div>
    <div class="chart-svg-wrap">
      <svg width="{w}" height="{h}" viewBox="0 0 {w} {h}" style="min-width:{w}px;">
        <line x1="{pad_l:.1f}" y1="{pad_t:.1f}" x2="{pad_l:.1f}" y2="{h - pad_b:.1f}" stroke="var(--gridline)" />
        <line x1="{pad_l:.1f}" y1="{h - pad_b:.1f}" x2="{w - pad_r:.1f}" y2="{h - pad_b:.1f}" stroke="var(--gridline)" />
        {y_ticks}
        {x_ticks}
        {"".join(level_lines)}
        <path d="{price_path}" fill="none" stroke="var(--call)" stroke-width="2.2" />
      </svg>
    </div>
    """


def render_html(label, date_str, current_price, levels, candles):
    template = WEEKLY_TEMPLATE_PATH.read_text()
    badge = {"Pre-Weekly": "PRE-WEEKLY", "Weekly Recap": "WEEKLY RECAP"}.get(label, "SNAPSHOT")
    title = f"BTC Weekly OI Levels — {label} — {date_str}"

    if label == "Pre-Weekly":
        subtitle = "Vorschau auf die Woche: aktuelle Top-Strike-OI-Level als Watch-Zonen für den BTC-Preis."
    elif label == "Weekly Recap":
        subtitle = "Rückblick: wurden die zuvor markierten OI-Level in den letzten 10 Tagen vom BTC-Preis erreicht?"
    else:
        subtitle = (
            "Zwischenstand außerhalb des Montag/Samstag-Rhythmus — zeigt die aktuellen "
            "Top-Strike-OI-Level und deren Treffer-Status."
        )

    total = len(levels)
    hit_count = sum(1 for l in levels if l.get("status") == "hit")
    chart_note = (
        "Preisdaten folgen ab nächstem Fetch-Lauf"
        if not candles
        else f"{hit_count} von {total} Level(s) getroffen (10 Tage)"
    )

    html = template
    html = html.replace("__REPORT_BADGE__", f"{badge} · {date_str}")
    html = html.replace("__REPORT_TITLE__", title)
    html = html.replace("__REPORT_SUBTITLE__", subtitle)
    html = html.replace("__KPI_BLOCK__", render_kpi_block(current_price, levels))
    html = html.replace("__CHART_NOTE__", chart_note)
    html = html.replace("__CHART_BLOCK__", render_chart_block(candles, levels))
    html = html.replace("__TABLE_BLOCK__", render_table_block(levels))
    return html


def build_levels(call_segments, put_segments, candles):
    call_latest = list(reversed(call_segments[-MAX_LEVELS_PER_SIDE:]))
    put_latest = list(reversed(put_segments[-MAX_LEVELS_PER_SIDE:]))
    levels = []
    for i, seg in enumerate(call_latest):
        levels.append({
            **seg,
            **check_level_hit(seg["strike"], candles),
            "direction": "Call",
            "age": "current" if i == 0 else "previous",
        })
    for i, seg in enumerate(put_latest):
        levels.append({
            **seg,
            **check_level_hit(seg["strike"], candles),
            "direction": "Put",
            "age": "current" if i == 0 else "previous",
        })
    return levels


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

    html = render_html(label, date_str, current_price, levels, candles)
    OUT_HTML_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_HTML_PATH.write_text(html)

    print(
        f"OK: {report_path} und {OUT_HTML_PATH} erzeugt "
        f"({len(levels)} Level, H1-Daten: {'ja' if candles else 'nein'}, Typ: {label})"
    )


if __name__ == "__main__":
    main()
