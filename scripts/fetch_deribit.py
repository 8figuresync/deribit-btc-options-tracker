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
from datetime import datetime, timezone
from pathlib import Path

URL = "https://www.deribit.com/api/v2/public/get_book_summary_by_currency?currency=BTC&kind=option"
OUT_PATH = Path("snapshots/incoming.json")
MIN_INSTRUMENTS = 50
MAX_ATTEMPTS = 3
TIMEOUT_SECONDS = 30


def fetch():
    last_error = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            req = urllib.request.Request(
                URL, headers={"User-Agent": "deribit-btc-options-tracker/1.0"}
            )
            with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as response:
                if response.status != 200:
                    raise RuntimeError(f"HTTP {response.status}")
                return json.loads(response.read())
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, json.JSONDecodeError) as exc:
            last_error = exc
            if attempt < MAX_ATTEMPTS:
                time.sleep(2 ** attempt)
    raise RuntimeError(f"Deribit API nach {MAX_ATTEMPTS} Versuchen nicht erreichbar: {last_error}")


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

    snapshot = {
        "timestamp_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "instruments": instruments,
    }

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = OUT_PATH.with_suffix(".json.tmp")
    tmp_path.write_text(json.dumps(snapshot, indent=2))
    tmp_path.replace(OUT_PATH)

    print(
        f"OK: {len(instruments)} Instrumente gespeichert nach {OUT_PATH} "
        f"(timestamp_utc={snapshot['timestamp_utc']})"
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"FEHLER: {exc}", file=sys.stderr)
        sys.exit(1)
