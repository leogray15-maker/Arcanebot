"""Connect to MT5 via MetaApi and pull real XAUUSD M5 history to a CSV.

Read-only: this NEVER places an order. It verifies your MetaApi connection,
prints the account so you can confirm it's a DEMO, and downloads candles into
the bot's CSV format so you can backtest on real data.

Setup
-----
1. pip install metaapi-cloud-sdk python-dotenv
2. Copy .env.example to .env and fill in (git-ignored, never commit):
       METAAPI_TOKEN=...        # API Access token from app.metaapi.cloud
       METAAPI_ACCOUNT_ID=...   # the account's id
3. python3 scripts/metaapi_pull.py --bars 60000 --out data/xauusd_m5.csv

--bars 60000 is ~7 months of M5; raise it for more (paginated, so it is slower).
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import os

import pandas as pd


def _load_env():
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except Exception:
        pass
    return os.getenv("METAAPI_TOKEN", ""), os.getenv("METAAPI_ACCOUNT_ID", ""), \
        os.getenv("METAAPI_DOMAIN", "agiliumtrade.agiliumtrade.ai")


async def pull(symbol: str, timeframe: str, bars: int, out: str) -> None:
    token, account_id, domain = _load_env()
    if not token or not account_id:
        raise SystemExit("Missing METAAPI_TOKEN / METAAPI_ACCOUNT_ID in .env")

    from metaapi_cloud_sdk import MetaApi
    api = MetaApi(token, {"domain": domain})
    account = await api.metatrader_account_api.get_account(account_id)

    print(f"Account: {getattr(account, 'name', '?')}  type={getattr(account, 'type', '?')}")
    if "demo" not in str(getattr(account, "type", "")).lower():
        print("[!] This does NOT look like a demo account. For safety this script only "
              "reads data, but please connect a DEMO account for anything involving orders.")

    await account.deploy()
    print("Deploying / waiting for connection…")
    await account.wait_connected()

    # Page backwards from now until we have `bars` candles.
    collected: list[dict] = []
    start_time = None
    while len(collected) < bars:
        page = await account.get_historical_candles(symbol, timeframe, start_time, 1000)
        if not page:
            break
        collected = page + collected
        start_time = page[0]["time"] - dt.timedelta(minutes=1)
        print(f"  …{len(collected):,} candles (back to {page[0]['time']})")

    if not collected:
        raise SystemExit("No candles returned. Check the symbol name for your broker.")

    # De-dupe and format.
    df = pd.DataFrame(collected)
    df["timestamp"] = pd.to_datetime(df["time"], utc=True)
    df = df.rename(columns={"tickVolume": "volume"})
    df = df[["timestamp", "open", "high", "low", "close", "volume"]]
    df = df.drop_duplicates("timestamp").sort_values("timestamp")

    out_dir = os.path.dirname(out)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    df.to_csv(out, index=False)
    print(f"Wrote {len(df):,} {symbol} {timeframe} bars "
          f"({df['timestamp'].iloc[0]} -> {df['timestamp'].iloc[-1]}) -> {out}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="XAUUSD")
    ap.add_argument("--timeframe", default="5m")
    ap.add_argument("--bars", type=int, default=60000)
    ap.add_argument("--out", default="data/xauusd_m5.csv")
    args = ap.parse_args()
    asyncio.run(pull(args.symbol, args.timeframe, args.bars, args.out))


if __name__ == "__main__":
    main()
