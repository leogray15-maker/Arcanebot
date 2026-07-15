"""Generate synthetic XAUUSD M5 candles so the pipeline is runnable end-to-end.

This is NOT market data and must never be used to judge the strategy — it exists
only to exercise the data -> structure -> signal -> backtest -> report path
before you drop in real IC Markets CSVs. It builds a random walk with modest
session-dependent volatility, skips weekends, and writes UTC timestamps in the
loader's expected schema.

    python scripts/generate_sample_data.py --days 20 --out data/sample_xauusd_m5.csv
"""

from __future__ import annotations

import argparse

import numpy as np
import pandas as pd


def generate(days: int, start: str, seed: int, base_price: float) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    start_ts = pd.Timestamp(start, tz="UTC").normalize()

    rows = []
    price = base_price
    day = start_ts
    produced = 0
    while produced < days:
        if day.weekday() >= 5:  # skip Sat/Sun
            day += pd.Timedelta(days=1)
            continue
        # 24h of M5 bars.
        for step in range(288):
            t = day + pd.Timedelta(minutes=5 * step)
            hour = t.hour
            # Wider moves during London (7-10) and NY AM (12-15).
            vol = 0.35
            if 7 <= hour < 10 or 12 <= hour < 15:
                vol = 0.9
            elif 0 <= hour < 7:
                vol = 0.5  # Asian
            drift = rng.normal(0, vol)
            o = price
            c = o + drift
            wick = abs(rng.normal(0, vol)) + 0.05
            h = max(o, c) + wick * rng.uniform(0.2, 1.0)
            l = min(o, c) - wick * rng.uniform(0.2, 1.0)
            v = int(abs(rng.normal(500, 200)) + 50)
            rows.append((t, o, h, l, c, v))
            price = c
        produced += 1
        day += pd.Timedelta(days=1)

    df = pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close", "volume"])
    return df


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=20)
    ap.add_argument("--start", default="2024-01-01")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--base-price", type=float, default=2350.0)
    ap.add_argument("--out", default="data/sample_xauusd_m5.csv")
    args = ap.parse_args()

    df = generate(args.days, args.start, args.seed, args.base_price)
    df.to_csv(args.out, index=False)
    print(f"Wrote {len(df)} M5 bars -> {args.out}")


if __name__ == "__main__":
    main()
