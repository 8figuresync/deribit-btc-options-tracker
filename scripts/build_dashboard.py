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

MONTHS = {"JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
          "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12}


def expiry_sort_key(expiry):
    m = re.match(r"(\d+)([A-Z]{3})(\d+)", expiry)
    day, mon, yr = int(m.group(1)), MONTHS[m.group(2)], int(m.group(3))
    return (2000 + yr, mon, day)


def parse_instrument(name):
    parts = name.split("-")
    return parts[1], float(parts[2]), parts[3]


def load_snapshots():
    files = sorted(glob.glob(SNAPSHOT_GLOB))
    snapshots = []
    for path in files:
        with open(path) as f:
            data = json.load(f)
        snapshots.append(data)
    snapshots.sort(key=lambda s: s["timestamp_utc"])
    return snapshots[-MAX_HISTORY:]


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

        row = by_expiry.setdefault(expiry, {"volume": 0.0, "call_oi": 0.0, "put_oi": 0.0})
        row["volume"] += vol
        strow = by_strike.setdefault(strike, {"call_oi": 0.0, "put_oi": 0.0})
        if typ == "C":
            row["call_oi"] += oi
            strow["call_oi"] += oi
            total_call_oi += oi
        else:
            row["put_oi"] += oi
            strow["put_oi"] += oi
            total_put_oi += oi

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
        })
    if prev_agg is not None:
        expiry_rows.sort(key=lambda r: -abs((r["vol_delta"] or 0) + (r["call_oi_delta"] or 0) + (r["put_oi_delta"] or 0)))
    else:
        expiry_rows.sort(key=lambda r: expiry_sort_key(r["expiry"]))

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
        "by_strike": strike_rows,
        "movers": movers,
    }


def main():
    snapshots = load_snapshots()
    if not snapshots:
        raise SystemExit("Keine Snapshots unter snapshots/*-UTC.json gefunden — Dashboard nicht erzeugt.")

    records = []
    prev_agg = None
    for snap in snapshots:
        agg = aggregate(snap)
        records.append(build_record(snap, agg, prev_agg))
        prev_agg = agg

    template = TEMPLATE_PATH.read_text()
    html = template.replace("__SNAPSHOTS_JSON__", json.dumps(records, separators=(",", ":")))

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(html)
    print(f"OK: {OUT_PATH} aus {len(records)} Snapshot(s) erzeugt (neuester: {records[-1]['timestamp_utc']})")


if __name__ == "__main__":
    main()
