#!/usr/bin/env python3
"""Rebuild docs/index.html from the archived snapshots under snapshots/.

Reads every timestamped archive (snapshots/<YYYY-MM-DD_HH-MM>-UTC.json,
written by the tracker routine's SCHRITT 5), computes the same aggregates as
the per-run Markdown report, and renders a self-contained HTML dashboard
with one entry per snapshot plus deltas vs. the previous one. No network
access and no third-party dependencies — safe to run as part of the
scheduled tracker routine (Bash/Read/Write only).
"""
import glob
import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SNAPSHOT_GLOB = str(REPO_ROOT / "snapshots" / "*-UTC.json")
TEMPLATE_PATH = REPO_ROOT / "scripts" / "dashboard_template.html"
OUT_PATH = REPO_ROOT / "docs" / "index.html"
MAX_HISTORY = 60
MAX_LEVELS_PER_SIDE = 2
HIT_TOLERANCE = 0.0025

MONTHS = {"JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
          "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12}


def expiry_sort_key(expiry):
    m = re.match(r"(\d+)([A-Z]{3})(\d+)", expiry)
    day, mon, yr = int(m.group(1)), MONTHS[m.group(2)], int(m.group(3))
    return (2000 + yr, mon, day)


def parse_instrument(name):
    parts = name.split("-")
    return parts[1], float(parts[2]), parts[3]


def load_all_snapshots():
    files = sorted(glob.glob(SNAPSHOT_GLOB))
    snapshots = []
    for path in files:
        with open(path) as f:
            data = json.load(f)
        snapshots.append(data)
    snapshots.sort(key=lambda s: s["timestamp_utc"])
    return snapshots


def load_snapshots():
    return load_all_snapshots()[-MAX_HISTORY:]


def aggregate(snapshot):
    by_expiry = {}
    by_strike = {}
    vol_by_instrument = {}
    total_call_oi = 0.0
    total_put_oi = 0.0
    total_volume = 0.0
    total_volume_usd = 0.0

    for inst in snapshot["instruments"]:
        name = inst["instrument_name"]
        expiry, strike, typ = parse_instrument(name)
        vol = inst.get("volume") or 0
        vol_usd = inst.get("volume_usd") or 0
        oi = inst.get("open_interest") or 0

        row = by_expiry.setdefault(expiry, {
            "volume": 0.0, "call_oi": 0.0, "put_oi": 0.0,
            "top_call_strike": None, "top_call_oi": 0.0,
            "top_put_strike": None, "top_put_oi": 0.0,
        })
        row["volume"] += vol
        strow = by_strike.setdefault(strike, {"call_oi": 0.0, "put_oi": 0.0})
        if typ == "C":
            row["call_oi"] += oi
            strow["call_oi"] += oi
            total_call_oi += oi
            if oi > row["top_call_oi"]:
                row["top_call_oi"] = oi
                row["top_call_strike"] = strike
        else:
            row["put_oi"] += oi
            strow["put_oi"] += oi
            total_put_oi += oi
            if oi > row["top_put_oi"]:
                row["top_put_oi"] = oi
                row["top_put_strike"] = strike

        total_volume += vol
        total_volume_usd += vol_usd
        vol_by_instrument[name] = {"expiry": expiry, "strike": strike, "type": typ, "volume": vol}

    return {
        "by_expiry": by_expiry,
        "by_strike": by_strike,
        "vol_by_instrument": vol_by_instrument,
        "total_call_oi": total_call_oi,
        "total_put_oi": total_put_oi,
        "total_oi": total_call_oi + total_put_oi,
        "pc_ratio": (total_put_oi / total_call_oi) if total_call_oi else None,
        "total_volume": total_volume,
        "total_volume_usd": total_volume_usd,
        "instrument_count": len(snapshot["instruments"]),
    }


def build_record(snapshot, agg, prev_agg):
    d = None
    if prev_agg is not None:
        d = {
            "total_call_oi": agg["total_call_oi"] - prev_agg["total_call_oi"],
            "total_put_oi": agg["total_put_oi"] - prev_agg["total_put_oi"],
            "total_oi": agg["total_oi"] - prev_agg["total_oi"],
            "pc_ratio": (agg["pc_ratio"] - prev_agg["pc_ratio"])
                        if (agg["pc_ratio"] is not None and prev_agg["pc_ratio"] is not None) else None,
            "total_volume": agg["total_volume"] - prev_agg["total_volume"],
        }

    expiry_rows = []
    for expiry, row in agg["by_expiry"].items():
        prev_row = prev_agg["by_expiry"].get(expiry) if prev_agg else None
        vol_delta = (row["volume"] - prev_row["volume"]) if prev_row else None
        call_delta = (row["call_oi"] - prev_row["call_oi"]) if prev_row else None
        put_delta = (row["put_oi"] - prev_row["put_oi"]) if prev_row else None
        expiry_rows.append({
            "expiry": expiry,
            "volume": round(row["volume"], 2),
            "call_oi": round(row["call_oi"], 2),
            "put_oi": round(row["put_oi"], 2),
            "vol_delta": round(vol_delta, 2) if vol_delta is not None else None,
            "call_oi_delta": round(call_delta, 2) if call_delta is not None else None,
            "put_oi_delta": round(put_delta, 2) if put_delta is not None else None,
            "top_call_strike": row["top_call_strike"],
            "top_put_strike": row["top_put_strike"],
        })
    if prev_agg is not None:
        expiry_rows.sort(key=lambda r: -abs((r["vol_delta"] or 0) + (r["call_oi_delta"] or 0) + (r["put_oi_delta"] or 0)))
    else:
        expiry_rows.sort(key=lambda r: expiry_sort_key(r["expiry"]))

    # OI-nach-Expiration-Panel: sortiere nach Naehe des Top-Call-/Top-Put-Strikes
    # zum aktuellen BTC-Preis (naehster Strike zuerst). Stable sort, also bleibt
    # die obige Sortierung als Fallback erhalten, wenn der Preis fehlt oder eine
    # Expiry weder Call- noch Put-Level hat.
    btc_index_price = snapshot.get("btc_index_price")
    if btc_index_price is not None:
        def _price_distance_key(r):
            dists = []
            if r["top_call_strike"] is not None:
                dists.append(abs(r["top_call_strike"] - btc_index_price))
            if r["top_put_strike"] is not None:
                dists.append(abs(r["top_put_strike"] - btc_index_price))
            return (1, 0.0) if not dists else (0, min(dists))
        expiry_rows_by_price_distance = sorted(expiry_rows, key=_price_distance_key)
    else:
        expiry_rows_by_price_distance = expiry_rows

    strike_rows = []
    for strike, row in agg["by_strike"].items():
        prev_row = prev_agg["by_strike"].get(strike) if prev_agg else None
        call_delta = (row["call_oi"] - prev_row["call_oi"]) if prev_row else None
        put_delta = (row["put_oi"] - prev_row["put_oi"]) if prev_row else None
        if not row["call_oi"] and not row["put_oi"]:
            continue
        strike_rows.append({
            "strike": strike,
            "call_oi": round(row["call_oi"], 2),
            "put_oi": round(row["put_oi"], 2),
            "call_oi_delta": round(call_delta, 2) if call_delta is not None else None,
            "put_oi_delta": round(put_delta, 2) if put_delta is not None else None,
        })
    if prev_agg is not None:
        strike_rows = [r for r in strike_rows if (r["call_oi_delta"] or 0) != 0 or (r["put_oi_delta"] or 0) != 0]
        strike_rows.sort(key=lambda r: -(abs(r["call_oi_delta"] or 0) + abs(r["put_oi_delta"] or 0)))
        strike_rows = strike_rows[:25]
    else:
        strike_rows.sort(key=lambda r: -(r["call_oi"] + r["put_oi"]))
        strike_rows = strike_rows[:25]
    strike_rows.sort(key=lambda r: r["strike"])

    movers = []
    if prev_agg is not None:
        prev_vol = prev_agg["vol_by_instrument"]
        names = set(agg["vol_by_instrument"]) | set(prev_vol)
        for name in names:
            now = agg["vol_by_instrument"].get(name)
            before = prev_vol.get(name)
            now_vol = now["volume"] if now else 0.0
            before_vol = before["volume"] if before else 0.0
            delta = now_vol - before_vol
            if delta == 0:
                continue
            meta = now or before
            movers.append({
                "name": name, "expiry": meta["expiry"], "strike": meta["strike"], "type": meta["type"],
                "before": round(before_vol, 2), "now": round(now_vol, 2), "delta": round(delta, 2),
                "delta_pct": round((delta / before_vol) * 100, 1) if before_vol else None,
            })
        movers.sort(key=lambda m: -abs(m["delta"]))
        movers = movers[:15]
    else:
        top = sorted(agg["vol_by_instrument"].items(), key=lambda kv: -kv[1]["volume"])[:15]
        top = [(n, v) for n, v in top if v["volume"] != 0]
        for name, v in top:
            movers.append({
                "name": name, "expiry": v["expiry"], "strike": v["strike"], "type": v["type"],
                "before": None, "now": round(v["volume"], 2), "delta": None, "delta_pct": None,
            })

    return {
        "timestamp_utc": snapshot["timestamp_utc"],
        "instrument_count": agg["instrument_count"],
        "is_baseline": prev_agg is None,
        "btc_index_price": snapshot.get("btc_index_price"),
        "btc_weekly_ranges": snapshot.get("btc_weekly_ranges"),
        "kpi": {
            "total_call_oi": round(agg["total_call_oi"], 2),
            "total_put_oi": round(agg["total_put_oi"], 2),
            "total_oi": round(agg["total_oi"], 2),
            "pc_ratio": round(agg["pc_ratio"], 4) if agg["pc_ratio"] is not None else None,
            "total_volume": round(agg["total_volume"], 2),
            "total_volume_usd": round(agg["total_volume_usd"], 2),
            "delta": {k: (round(v, 4) if k == "pc_ratio" and v is not None else (round(v, 2) if v is not None else None))
                      for k, v in d.items()} if d else None,
        },
        "by_expiry": expiry_rows,
        "by_expiry_oi": expiry_rows_by_price_distance,
        "by_strike": strike_rows,
        "movers": movers,
    }


def build_market_delta_point(snapshot):
    return {
        "timestamp_utc": snapshot["timestamp_utc"],
        "btc_index_price": snapshot.get("btc_index_price"),
    }


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


def build_levels(call_segments, put_segments, candles):
    """Up to MAX_LEVELS_PER_SIDE most recent distinct Call/Put OI levels
    (current + previous), each with its hit-status against the last H1
    candles."""
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


def build_oi_levels(all_snapshots):
    latest = all_snapshots[-1]
    candles = latest.get("btc_h1_candles") or []
    call_series, put_series = build_call_put_series(all_snapshots)
    call_segments = build_segments(call_series)
    put_segments = build_segments(put_series)
    return build_levels(call_segments, put_segments, candles)


def main():
    all_snapshots = load_all_snapshots()
    if not all_snapshots:
        raise SystemExit("Keine Snapshots unter snapshots/*-UTC.json gefunden — Dashboard nicht erzeugt.")
    snapshots = all_snapshots[-MAX_HISTORY:]

    records = []
    market_delta = []
    prev_agg = None
    for snap in snapshots:
        agg = aggregate(snap)
        records.append(build_record(snap, agg, prev_agg))
        market_delta.append(build_market_delta_point(snap))
        prev_agg = agg

    oi_levels = build_oi_levels(all_snapshots)

    template = TEMPLATE_PATH.read_text()
    html = template.replace("__SNAPSHOTS_JSON__", json.dumps(records, separators=(",", ":")))
    html = html.replace("__MARKET_DELTA_JSON__", json.dumps(market_delta, separators=(",", ":")))
    html = html.replace("__OI_LEVELS_JSON__", json.dumps(oi_levels, separators=(",", ":")))

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(html)
    print(f"OK: {OUT_PATH} aus {len(records)} Snapshot(s) erzeugt (neuester: {records[-1]['timestamp_utc']}, {len(oi_levels)} OI-Level)")


if __name__ == "__main__":
    main()
