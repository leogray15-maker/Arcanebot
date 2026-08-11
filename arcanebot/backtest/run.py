"""Phase 4/6 — Backtest CLI.

    python -m arcanebot.backtest.run --m5 data/xauusd_m5.csv
    python -m arcanebot.backtest.run --m5 data/xauusd_m5.csv --m15 data/xauusd_m15.csv \
        --report --outdir outputs

If --m15 is omitted it is resampled from the M5 file. Writes a trade-log CSV and
prints summary stats; --report also renders the equity curve / R distribution.
"""

from __future__ import annotations

import argparse
import os

import pandas as pd

from config import DEFAULT_CONFIG
from arcanebot.data import load_candles, resample
from arcanebot.backtest.engine import run_backtest
from arcanebot.backtest.stats import per_session, summarize


def main() -> None:
    ap = argparse.ArgumentParser(description="Arcanebot XAUUSD backtest")
    ap.add_argument("--m5", required=True, help="M5 CSV path")
    ap.add_argument("--m15", help="M15 CSV path (resampled from M5 if omitted)")
    ap.add_argument("--outdir", default="outputs")
    ap.add_argument("--report", action="store_true", help="also render PNG plots")
    ap.add_argument("--no-dashboard", action="store_true", help="skip the HTML dashboard")
    ap.add_argument("--serve", action="store_true", help="serve the dashboard on localhost and open it")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--no-validate", action="store_true")
    args = ap.parse_args()

    cfg = DEFAULT_CONFIG
    os.makedirs(args.outdir, exist_ok=True)

    m5 = load_candles(args.m5, cfg.data.base_timeframe, cfg.data, validate=not args.no_validate)
    if args.m15:
        m15 = load_candles(args.m15, cfg.data.htf_timeframe, cfg.data, validate=not args.no_validate)
    else:
        m15 = resample(m5, cfg.data.htf_timeframe, cfg.data)

    print(f"Loaded {len(m5)} M5 bars, {len(m15)} M15 bars "
          f"({m5.index[0]} -> {m5.index[-1]})")

    result = run_backtest(m5, m15, cfg)
    summary = summarize(result.trades, result.equity_curve)

    trades_df = result.trades_df()
    trade_path = os.path.join(args.outdir, "trades.csv")
    trades_df.to_csv(trade_path, index=False)
    log_path = os.path.join(args.outdir, "decisions.log")
    with open(log_path, "w") as fh:
        fh.write("\n".join(result.decision_logs))

    print("\n=== Summary ===")
    print(summary.format())
    if summary.expectancy_r > 2.0:
        print("\n[!] Expectancy > 2R — be skeptical: check for look-ahead, "
              "unrealistic fills, or curve-fit params before trusting this.")

    ps = per_session(result.trades)
    if not ps.empty:
        print("\n=== Per session ===")
        print(ps.to_string(float_format=lambda x: f"{x:.3f}"))

    print(f"\nTrade log:   {trade_path}")
    print(f"Decisions:   {log_path}")

    dash_path = None
    if not args.no_dashboard:
        from arcanebot.reporting.dashboard import write_dashboard
        dash_path = os.path.join(args.outdir, "dashboard.html")
        write_dashboard(result, dash_path, cfg)
        print(f"Dashboard:   {dash_path}")

    if args.report:
        from arcanebot.reporting.report import render_report
        paths = render_report(result, args.outdir)
        for p in paths:
            print(f"Report:      {p}")

    if args.serve and dash_path:
        _serve(args.outdir, args.port)


def _serve(outdir: str, port: int) -> None:
    """Serve the outdir on localhost and open the dashboard in a browser."""
    import functools
    import http.server
    import socketserver
    import webbrowser

    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=outdir)
    url = f"http://localhost:{port}/dashboard.html"
    with socketserver.TCPServer(("", port), handler) as httpd:
        print(f"\nServing dashboard at {url}  (Ctrl+C to stop)")
        try:
            webbrowser.open(url)
        except Exception:
            pass
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nStopped.")


if __name__ == "__main__":
    main()
