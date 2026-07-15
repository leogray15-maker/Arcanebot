"""Phase 6 — Reporting: equity curve, R distribution, per-session breakdown.

Uses matplotlib's non-interactive Agg backend so it runs headless (CI / web
sessions). Returns the paths of the artefacts it writes.
"""

from __future__ import annotations

import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from arcanebot.backtest.engine import BacktestResult  # noqa: E402
from arcanebot.backtest.stats import per_session, summarize  # noqa: E402


def render_report(result: BacktestResult, outdir: str) -> list[str]:
    os.makedirs(outdir, exist_ok=True)
    paths: list[str] = []

    # 1) Equity curve.
    fig, ax = plt.subplots(figsize=(10, 4))
    result.equity_curve.plot(ax=ax, color="#1f77b4")
    ax.set_title("Equity curve")
    ax.set_ylabel("Equity (USD)")
    ax.grid(True, alpha=0.3)
    p = os.path.join(outdir, "equity_curve.png")
    fig.tight_layout(); fig.savefig(p, dpi=120); plt.close(fig)
    paths.append(p)

    trades = result.trades
    if trades:
        # 2) R-multiple distribution.
        rs = [t.r_multiple for t in trades]
        fig, ax = plt.subplots(figsize=(8, 4))
        ax.hist(rs, bins=20, color="#2ca02c", edgecolor="white")
        ax.axvline(0, color="black", lw=1)
        ax.set_title("Distribution of R multiples")
        ax.set_xlabel("R")
        ax.set_ylabel("Trades")
        p = os.path.join(outdir, "r_distribution.png")
        fig.tight_layout(); fig.savefig(p, dpi=120); plt.close(fig)
        paths.append(p)

        # 3) Per-session expectancy.
        ps = per_session(trades)
        if not ps.empty:
            fig, ax = plt.subplots(figsize=(6, 4))
            ps["expectancy_r"].plot(kind="bar", ax=ax, color="#ff7f0e")
            ax.axhline(0, color="black", lw=1)
            ax.set_title("Expectancy (R) by session")
            ax.set_ylabel("Expectancy R")
            p = os.path.join(outdir, "per_session.png")
            fig.tight_layout(); fig.savefig(p, dpi=120); plt.close(fig)
            paths.append(p)

    # 4) Text summary alongside the plots.
    summary = summarize(trades, result.equity_curve)
    p = os.path.join(outdir, "summary.txt")
    with open(p, "w") as fh:
        fh.write(summary.format() + "\n")
    paths.append(p)
    return paths
