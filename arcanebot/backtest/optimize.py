"""Parameter sweep + walk-forward optimisation.

Two tools:

* ``sweep`` — run the backtest across a grid of parameters and rank the results.
  Fast, but on its own it OVERFITS: the best in-sample row is usually luck.

* ``walk_forward`` — the honest optimiser. Split history into folds; on each
  step pick the best parameters on the PAST (train) and score them on the NEXT
  unseen slice (test). Aggregating the out-of-sample slices tells you whether the
  edge survives on data it was never tuned on. If walk-forward expectancy is
  positive and close to the in-sample number, the edge is plausibly real; if it
  collapses, you curve-fit.

Nothing here can manufacture an edge that isn't in the data — on random/synthetic
candles both tools should return ~0 or negative. That's the point.
"""

from __future__ import annotations

import itertools
from dataclasses import replace

import pandas as pd

from config import Config, DEFAULT_CONFIG
from arcanebot.backtest.engine import run_backtest
from arcanebot.backtest.stats import summarize
from arcanebot.data import resample


def apply_overrides(cfg: Config, overrides: dict) -> Config:
    """Apply dotted overrides like {'risk.fixed_r_target': 3.0} to a Config."""
    sections: dict[str, dict] = {}
    for key, val in overrides.items():
        sec, field = key.split(".", 1)
        sections.setdefault(sec, {})[field] = val
    new_sections = {sec: replace(getattr(cfg, sec), **fields) for sec, fields in sections.items()}
    return replace(cfg, **new_sections)


def _grid_combos(grid: dict):
    keys = list(grid)
    for values in itertools.product(*[grid[k] for k in keys]):
        yield dict(zip(keys, values))


def _row(overrides: dict, s) -> dict:
    return {
        **overrides,
        "trades": s.trades,
        "win_rate": round(s.win_rate, 4),
        "expectancy_r": round(s.expectancy_r, 4),
        "profit_factor": round(s.profit_factor, 3),
        "total_pnl": round(s.total_pnl, 2),
        "max_dd_pct": round(s.max_drawdown_pct, 4),
    }


def sweep(m5: pd.DataFrame, m15: pd.DataFrame, grid: dict,
          base_cfg: Config = DEFAULT_CONFIG, rank: str = "expectancy_r") -> pd.DataFrame:
    """In-sample grid search. Returns one row per combo, ranked by ``rank``."""
    rows = []
    for ov in _grid_combos(grid):
        cfg = apply_overrides(base_cfg, ov)
        res = run_backtest(m5, m15, cfg)
        rows.append(_row(ov, summarize(res.trades, res.equity_curve)))
    df = pd.DataFrame(rows)
    return df.sort_values(rank, ascending=False).reset_index(drop=True) if not df.empty else df


def walk_forward(m5: pd.DataFrame, grid: dict, base_cfg: Config = DEFAULT_CONFIG,
                 folds: int = 5, min_trades: int = 8, select: str = "expectancy_r"):
    """Expanding-window walk-forward. Tune on the past, score on the next slice.

    Returns (per_fold_df, oos_summary). ``oos_summary`` aggregates every
    out-of-sample trade — the number to trust.
    """
    n = len(m5)
    bounds = [int(n * k / folds) for k in range(folds + 1)]
    records = []
    oos_trades = []

    for k in range(1, folds):
        train = m5.iloc[: bounds[k]]
        test = m5.iloc[bounds[k]: bounds[k + 1]]
        if len(test) < 50:
            continue

        best_ov, best_score, best_cfg = None, float("-inf"), None
        for ov in _grid_combos(grid):
            cfg = apply_overrides(base_cfg, ov)
            res = run_backtest(train, resample(train, cfg.data.htf_timeframe), cfg)
            s = summarize(res.trades, res.equity_curve)
            score = getattr(s, select)
            if s.trades >= min_trades and score > best_score:
                best_ov, best_score, best_cfg = ov, score, cfg
        if best_cfg is None:
            continue

        te = run_backtest(test, resample(test, best_cfg.data.htf_timeframe), best_cfg)
        te_s = summarize(te.trades, te.equity_curve)
        oos_trades.extend(te.trades)
        records.append({
            "fold": k, "train_score": round(best_score, 4), **best_ov,
            "oos_trades": te_s.trades, "oos_expectancy_r": round(te_s.expectancy_r, 4),
            "oos_win_rate": round(te_s.win_rate, 4), "oos_profit_factor": round(te_s.profit_factor, 3),
        })

    oos_summary = summarize(oos_trades, pd.Series(dtype=float))
    return pd.DataFrame(records), oos_summary


# Default grid — a sane starting search space including the profitability
# levers (HTF trend filter, breakeven, partial TP). Edit freely; every extra
# dimension multiplies runtime, so trim before a heavy walk-forward.
DEFAULT_GRID = {
    "structure.swing_n": [2, 3],
    "risk.tp_mode": ["liquidity", "fixed_r"],
    "risk.fixed_r_target": [2.0, 3.0],
    "signal.htf_trend_filter": [False, True],
    "risk.breakeven_at_r": [None, 1.0],
    "risk.partial_tp_r": [None, 2.0],
}


def _main() -> None:
    import argparse
    ap = argparse.ArgumentParser(description="Arcanebot parameter optimisation")
    ap.add_argument("--m5", required=True)
    ap.add_argument("--mode", choices=["sweep", "walk"], default="walk")
    ap.add_argument("--folds", type=int, default=5)
    ap.add_argument("--out", default="outputs/optimization.csv")
    args = ap.parse_args()

    from arcanebot.data import load_candles
    cfg = DEFAULT_CONFIG
    m5 = load_candles(args.m5, cfg.data.base_timeframe, cfg.data)
    print(f"Loaded {len(m5)} M5 bars ({m5.index[0]} -> {m5.index[-1]})")

    if args.mode == "sweep":
        df = sweep(m5, resample(m5, cfg.data.htf_timeframe), DEFAULT_GRID, cfg)
        print("\nTop 10 in-sample (WILL overfit — validate with --mode walk):")
        print(df.head(10).to_string(index=False))
        df.to_csv(args.out, index=False)
    else:
        per_fold, oos = walk_forward(m5, DEFAULT_GRID, cfg, folds=args.folds)
        print("\nPer-fold (params chosen on train, scored out-of-sample):")
        print(per_fold.to_string(index=False) if not per_fold.empty else "  (not enough data/trades)")
        print("\n=== OUT-OF-SAMPLE aggregate (the number to trust) ===")
        print(oos.format())
        if oos.expectancy_r <= 0:
            print("\n[!] Out-of-sample expectancy <= 0: no edge survived. Do NOT trade this.")
        per_fold.to_csv(args.out, index=False)

    print(f"\nSaved: {args.out}")


if __name__ == "__main__":
    _main()
