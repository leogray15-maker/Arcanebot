"""Download real XAUUSD history free from Dukascopy and write an M5 CSV.

No account or API key needed. Dukascopy publishes hourly tick files; this script
fetches them over a date range, decompresses, aggregates ticks into M5 candles
(mid price), and writes the loader's CSV schema.

    python scripts/download_data.py --symbol XAUUSD --start 2024-01-01 --end 2024-06-01 \
        --out data/xauusd_m5.csv

Notes
-----
* Weekends / missing hours (404) are skipped automatically.
* Gold is stored by Dukascopy with 3 decimals -> price = raw / 1000 (--scale).
* A few years of M5 is a lot of small requests; it is polite + slow. Start with
  a few months to validate the pipeline, then widen.
"""

from __future__ import annotations

import argparse
import datetime as dt
import lzma
import os
import struct
import urllib.error
import urllib.request

import numpy as np
import pandas as pd

BASE = "https://datafeed.dukascopy.com/datafeed/{sym}/{y}/{m0:02d}/{d:02d}/{h:02d}h_ticks.bi5"


def _fetch_hour(sym: str, day: dt.date, hour: int, timeout: float) -> bytes | None:
    url = BASE.format(sym=sym, y=day.year, m0=day.month - 1, d=day.day, h=hour)
    req = urllib.request.Request(url, headers={"User-Agent": "arcanebot-downloader"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise
    except urllib.error.URLError:
        return None


def _parse_ticks(raw: bytes, day: dt.date, hour: int, scale: float):
    """Return (times_ns, mid_prices) from a decompressed bi5 hour file."""
    if not raw:
        return [], []
    data = lzma.decompress(raw, format=lzma.FORMAT_AUTO)
    n = len(data) // 20
    hour_start = pd.Timestamp(dt.datetime(day.year, day.month, day.day, hour), tz="UTC")
    times, mids = [], []
    for i in range(n):
        ms, ask, bid, _av, _bv = struct.unpack_from(">IIIff", data, i * 20)
        times.append(hour_start + pd.Timedelta(milliseconds=ms))
        mids.append((ask + bid) / 2.0 / scale)
    return times, mids


def download(sym: str, start: dt.date, end: dt.date, scale: float, timeout: float) -> pd.DataFrame:
    all_times, all_mids = [], []
    day = start
    n_hours = 0
    while day < end:
        if day.weekday() < 5:  # skip weekends
            for hour in range(24):
                raw = _fetch_hour(sym, day, hour, timeout)
                if raw:
                    t, m = _parse_ticks(raw, day, hour, scale)
                    all_times.extend(t)
                    all_mids.extend(m)
                n_hours += 1
                if n_hours % 100 == 0:
                    print(f"  ...{n_hours} hours fetched ({day} {hour:02d}h), "
                          f"{len(all_times):,} ticks")
        day += dt.timedelta(days=1)

    if not all_times:
        return pd.DataFrame()

    ticks = pd.Series(all_mids, index=pd.DatetimeIndex(all_times)).sort_index()
    o = ticks.resample("5min").first()
    h = ticks.resample("5min").max()
    l = ticks.resample("5min").min()
    c = ticks.resample("5min").last()
    v = ticks.resample("5min").count()
    df = pd.DataFrame({"open": o, "high": h, "low": l, "close": c, "volume": v}).dropna()
    df.index.name = "timestamp"
    return df.reset_index()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="XAUUSD")
    ap.add_argument("--start", required=True, help="YYYY-MM-DD (inclusive)")
    ap.add_argument("--end", required=True, help="YYYY-MM-DD (exclusive)")
    ap.add_argument("--scale", type=float, default=1000.0, help="price divisor (gold=1000)")
    ap.add_argument("--timeout", type=float, default=30.0)
    ap.add_argument("--out", default="data/xauusd_m5.csv")
    args = ap.parse_args()

    start = dt.date.fromisoformat(args.start)
    end = dt.date.fromisoformat(args.end)
    print(f"Downloading {args.symbol} ticks {start} -> {end} from Dukascopy…")
    df = download(args.symbol, start, end, args.scale, args.timeout)
    if df.empty:
        print("No data returned. Check the symbol/date range/network.")
        return
    out_dir = os.path.dirname(args.out)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    df.to_csv(args.out, index=False)
    print(f"Wrote {len(df):,} M5 bars ({df['timestamp'].iloc[0]} -> "
          f"{df['timestamp'].iloc[-1]}) -> {args.out}")


if __name__ == "__main__":
    main()
