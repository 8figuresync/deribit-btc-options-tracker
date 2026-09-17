#!/usr/bin/env python3
"""Fetch the current BTC options book summary from Deribit and store it as
snapshots/incoming.json for the scheduled Claude Code task to pick up.

This runs on a GitHub Actions runner (normal internet access), not inside
the Claude Code cloud session, because that session's network egress policy
blocks www.deribit.com. On any failure it exits non-zero and leaves the
existing snapshots/incoming.json untouched, so the tracker never commits
partial or fabricated data.
"""
import json
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

URL = "https://www.deribit.com/api/v2/public/get_book_summary_by_currency?currency=BTC&kind=option"
INDEX_PRICE_URL = "https://www.deribit.com/api/v2/public/get_index_price?index_name=btc_usd"
CHART_URL_TEMPLATE = (
    "https://www.deribit.com/api/v2/public/get_tradingview_chart_data"
    "?instrument_name=BTC-PERPETUAL&resolution=1D&start_timestamp={start}&end_timestamp={end}"
)
OUT_PATH = Path("snapshots/incoming.json")
MIN_INSTRUMENTS = 50
MAX_ATTEMPTS = 3
TIMEOUT_SECONDS = 30
WEEKLY_HISTORY_DAYS = 90
WEEKLY_RANGES_KEEP = 10


def fetch_json(url):
    last_error = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": "deribit-btc-options-tracker/1.0"}
            )
            with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as response:
                if response.status != 200:
                    raise RuntimeError(f"HTTP {response.status}")
                return json.loads(response.read())
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, json.JSONDecodeError) as exc:
            last_error = exc
            if attempt < MAX_ATTEMPTS:
                time.sleep(2 ** attempt)
    raise RuntimeError(f"Deribit API ({url}) nach {MAX_ATTEMPTS} Versuchen nicht erreichbar: {last_error}")


def fetch():
    return fetch_json(URL)


def fetch_index_price():
    payload = fetch_json(INDEX_PRICE_URL)
    result = payload.get("result")
    if not isinstance(result, dict) or not isinstance(result.get("index_price"), (int, float)):
        raise RuntimeError(f"Unerwartete Antwort von get_index_price: {result!r}")
    return float(result["index_price"])


def fetch_weekly_ranges(current_price):
    now = datetime.now(timezone.utc)
    end_ms = int(now.timestamp() * 1000)
    start_ms = int((now - timedelta(days=WEEKLY_HISTORY_DAYS)).timestamp() * 1000)
    url = CHART_URL_TEMPLATE.format(start=start_ms, end=end_ms)
    payload = fetch_json(url)
    result = payload.get("result")
    if not isinstance(result, dict) or result.get("status") != "ok":
        raise RuntimeError(f"Unerwartete/fehlerhafte Antwort von get_tradingview_chart_data: {result!r}")

    ticks = result.get("ticks") or []
    opens = result.get("open") or []
    highs = result.get("high") or []
    lows = result.get("low") or []
    closes = result.get("close") or []
    if not ticks or not (len(ticks) == len(opens) == len(highs) == len(lows) == len(closes)):
        raise RuntimeError("Unvollstaendige/leere Tages-Candle-Arrays von get_tradingview_chart_data")

    weeks = {}
    for i, ts_ms in enumerate(ticks):
        day = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).date()
        week_start = day - timedelta(days=day.weekday())
        w = weeks.get(week_start)
        if w is None:
            weeks[week_start] = {
                "week_start": week_start.isoformat(),
                "open": opens[i],
                "high": highs[i],
                "low": lows[i],
                "close": closes[i],
                "_first_ts": ts_ms,
                "_last_ts": ts_ms,
            }
        else:
            w["high"] = max(w["high"], highs[i])
            w["low"] = min(w["low"], lows[i])
            if ts_ms < w["_first_ts"]:
                w["open"] = opens[i]
                w["_first_ts"] = ts_ms
            if ts_ms >= w["_last_ts"]:
                w["close"] = closes[i]
                w["_last_ts"] = ts_ms

    weekly = [weeks[k] for k in sorted(weeks)]
    for w in weekly:
        del w["_first_ts"]
        del w["_last_ts"]

    today = now.date()
    current_week_start = (today - timedelta(days=today.weekday())).isoformat()
    if weekly and weekly[-1]["week_start"] == current_week_start:
        current_week = weekly[-1]
        current_week["close"] = current_price
        current_week["high"] = max(current_week["high"], current_price)
        current_week["low"] = min(current_week["low"], current_price)

    return weekly[-WEEKLY_RANGES_KEEP:]


def main():
    payload = fetch()

    result = payload.get("result")
    if not isinstance(result, list) or len(result) < MIN_INSTRUMENTS:
        got = len(result) if isinstance(result, list) else "kein"
        raise RuntimeError(
            f"Unerwartete/leere API-Antwort: result hat {got} Eintraege "
            f"(erwartet >= {MIN_INSTRUMENTS})"
        )

    instruments = []
    for item in result:
        name = item.get("instrument_name")
        if not name:
            continue
        instruments.append(
            {
                "instrument_name": name,
                "volume": item.get("volume"),
                "volume_usd": item.get("volume_usd"),
                "open_interest": item.get("open_interest"),
            }
        )

    index_price = fetch_index_price()
    weekly_ranges = fetch_weekly_ranges(index_price)

    snapshot = {
        "timestamp_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "instruments": instruments,
        "btc_index_price": index_price,
        "btc_weekly_ranges": weekly_ranges,
    }

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = OUT_PATH.with_suffix(".json.tmp")
    tmp_path.write_text(json.dumps(snapshot, indent=2))
    tmp_path.replace(OUT_PATH)

    print(
        f"OK: {len(instruments)} Instrumente gespeichert nach {OUT_PATH} "
        f"(timestamp_utc={snapshot['timestamp_utc']}, btc_index_price={index_price}, "
        f"wochen={len(weekly_ranges)})"
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"FEHLER: {exc}", file=sys.stderr)
        sys.exit(1)
