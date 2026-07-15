"""Phase 4 — Summary statistics for a backtest run.

Win rate, average R, profit factor, expectancy, max drawdown, plus a per-session
breakdown and the fixed-R shadow comparison. Deliberately plain arithmetic so the
numbers are auditable — and so an implausibly good result (the spec's >~2R
expectancy warning) is easy to spot.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from arcanebot.backtest.broker import Trade


@dataclass
class Summary:
    trades: int
    wins: int
    losses: int
    win_rate: float
    avg_r: float
    expectancy_r: float
    profit_factor: float
    total_pnl: float
    max_drawdown: float
    max_drawdown_pct: float
    avg_win_r: float
    avg_loss_r: float
    # fixed-R shadow
    shadow_expectancy_r: float | None = None
    shadow_win_rate: float | None = None

    def as_dict(self) -> dict:
        return self.__dict__.copy()

    def format(self) -> str:
        lines = [
            f"Trades:            {self.trades}",
            f"Win rate:          {self.win_rate:.1%}  ({self.wins}W / {self.losses}L)",
            f"Avg R / trade:     {self.avg_r:+.2f}R",
            f"Expectancy:        {self.expectancy_r:+.2f}R",
            f"Profit factor:     {self.profit_factor:.2f}",
            f"Total PnL:         {self.total_pnl:+,.2f}",
            f"Max drawdown:      {self.max_drawdown:,.2f} ({self.max_drawdown_pct:.1%})",
            f"Avg win / loss:    {self.avg_win_r:+.2f}R / {self.avg_loss_r:+.2f}R",
        ]
        if self.shadow_expectancy_r is not None:
            lines.append(
                f"Fixed-R shadow:    {self.shadow_expectancy_r:+.2f}R expectancy, "
                f"{self.shadow_win_rate:.1%} win rate"
            )
        return "\n".join(lines)


def _drawdown(equity: pd.Series) -> tuple[float, float]:
    if equity.empty:
        return 0.0, 0.0
    running_max = equity.cummax()
    dd = equity - running_max
    max_dd = float(dd.min())
    # As a fraction of the peak at the trough.
    trough_idx = dd.idxmin()
    peak = float(running_max.loc[trough_idx])
    pct = (max_dd / peak) if peak else 0.0
    return max_dd, pct


def summarize(trades: list[Trade], equity: pd.Series) -> Summary:
    n = len(trades)
    if n == 0:
        return Summary(0, 0, 0, 0.0, 0.0, 0.0, 0.0, 0.0, *(_drawdown(equity)),
                       0.0, 0.0)

    rs = [t.r_multiple for t in trades]
    pnls = [t.pnl for t in trades]
    wins = [r for r in rs if r > 0]
    losses = [r for r in rs if r <= 0]
    gross_win = sum(p for p in pnls if p > 0)
    gross_loss = -sum(p for p in pnls if p < 0)

    max_dd, dd_pct = _drawdown(equity)

    shadow_rs = [t.shadow_r_multiple for t in trades if t.shadow_r_multiple is not None]
    shadow_exp = (sum(shadow_rs) / len(shadow_rs)) if shadow_rs else None
    shadow_wr = (len([r for r in shadow_rs if r > 0]) / len(shadow_rs)) if shadow_rs else None

    return Summary(
        trades=n,
        wins=len(wins),
        losses=len(losses),
        win_rate=len(wins) / n,
        avg_r=sum(rs) / n,
        expectancy_r=sum(rs) / n,
        profit_factor=(gross_win / gross_loss) if gross_loss > 0 else float("inf"),
        total_pnl=sum(pnls),
        max_drawdown=max_dd,
        max_drawdown_pct=dd_pct,
        avg_win_r=(sum(wins) / len(wins)) if wins else 0.0,
        avg_loss_r=(sum(losses) / len(losses)) if losses else 0.0,
        shadow_expectancy_r=shadow_exp,
        shadow_win_rate=shadow_wr,
    )


def per_session(trades: list[Trade]) -> pd.DataFrame:
    if not trades:
        return pd.DataFrame()
    df = pd.DataFrame([t.__dict__ for t in trades])
    grp = df.groupby("session").agg(
        trades=("r_multiple", "size"),
        win_rate=("r_multiple", lambda s: (s > 0).mean()),
        expectancy_r=("r_multiple", "mean"),
        total_pnl=("pnl", "sum"),
    )
    return grp
