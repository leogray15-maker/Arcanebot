# Arcanebot — Pine Script (TradingView)

`arcanebot_ict.pine` is a **Pine v5 strategy** port of the Python bot's core
logic, for visual confirmation and rough backtesting inside TradingView.

## How to use

1. Open TradingView, load **XAUUSD** on the **5-minute** chart.
2. Open the **Pine Editor** (bottom panel), paste the contents of
   `arcanebot_ict.pine`, and click **Add to chart**.
3. Open the **Strategy Tester** tab to see the backtest, or watch the chart for
   the `sweep` / `MSS` labels, shaded kill zones, and green/red FVG entry boxes.
4. Tune inputs (swing N, kill-zone hours, SL buffer, TP mode, R target) via the
   gear icon — they mirror `config.py`.

## What it does (mirrors the Python engine)

- Kill zones: London 07:00–10:00 and NY-AM 12:00–15:00 **UTC** (configurable).
- Liquidity sweep of the last swing low/high, reclaim on close.
- MSS: close beyond the last opposing swing.
- Bullish/bearish **FVG** entry (3-candle gap) with a displacement filter.
- SL beyond the sweep extreme (+ buffer); TP at fixed-R or the opposing swing.
- Evaluates on **closed bars**; pivots confirm N bars later, so it does not
  repaint.

## What it is NOT

This is a companion, not a 1:1 clone. Pine can't reproduce the Python engine's
M15-derived multi-source liquidity pool, equal-level clustering, prior-day
levels, or the exact spread/slippage fill model. TradingView's tester also
models commission/fills its own way. **Use the Python backtest for the numbers
you trust; use this to eyeball setups on a live chart.**

Set realistic **commission + slippage** in the strategy's Properties tab before
reading its stats, and remember: results that look too good usually mean a bug.
