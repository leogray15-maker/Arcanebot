"""Phase 4 — Event-driven backtest loop.

Drives one closed M5 bar at a time:

    for each bar i:
        1. broker.process_bar(i)   # fills / SL / TP from orders placed earlier
        2. engine.evaluate(i)      # decide on the just-closed bar
        3. apply the decision (submit / cancel) to the broker

Step 1 runs before step 2 so an order can never fill on the same bar that
created it, and the signal engine only ever sees closed bars — the two
structural guarantees the Phase 5 look-ahead test checks.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from config import Config, DEFAULT_CONFIG
from arcanebot.backtest.broker import BacktestBroker, Trade
from arcanebot.execution.base import Side
from arcanebot.signals.engine import SignalEngine


@dataclass
class BacktestResult:
    trades: list[Trade]
    equity_curve: pd.Series          # indexed by bar close-ish timestamp
    decision_logs: list[str]
    cfg: Config

    def trades_df(self) -> pd.DataFrame:
        if not self.trades:
            return pd.DataFrame()
        rows = [t.__dict__ for t in self.trades]
        return pd.DataFrame(rows)


def run_backtest(
    m5: pd.DataFrame,
    m15: pd.DataFrame,
    cfg: Config = DEFAULT_CONFIG,
    close_open_at_end: bool = True,
) -> BacktestResult:
    engine = SignalEngine(cfg)
    engine.prepare(m5, m15)
    broker = BacktestBroker(cfg)

    logs: list[str] = []
    curve_times: list[pd.Timestamp] = []
    curve_equity: list[float] = []

    for i in range(len(m5)):
        bar = m5.iloc[i]
        t = m5.index[i]

        events = broker.process_bar(bar, i, t)
        for e in events:
            if e == "filled":
                engine.notify_filled()
            elif e == "cancelled_daily":
                engine.notify_cancelled()

        decision = engine.evaluate(i, broker.view())
        if decision.cancel_reason:
            broker.cancel_pending(decision.cancel_reason)
            engine.notify_cancelled()
        if decision.submit is not None:
            broker.submit(decision.submit)
        logs.extend(decision.logs)

        curve_times.append(t)
        curve_equity.append(broker.equity())

    if close_open_at_end:
        broker.close_open_at_end(m5)
        if curve_equity:
            curve_equity[-1] = broker.equity()

    # Fixed-R shadow outcome on every trade (comparison of exit styles).
    if cfg.risk.always_log_fixed_r_shadow:
        for tr in broker.trades:
            _attach_fixed_r_shadow(m5, tr, cfg)

    equity = pd.Series(curve_equity, index=pd.DatetimeIndex(curve_times, name="timestamp"))
    return BacktestResult(broker.trades, equity, logs, cfg)


def _attach_fixed_r_shadow(m5: pd.DataFrame, tr: Trade, cfg: Config) -> None:
    """What a fixed-R exit would have returned on the same entry/stop."""
    hs = cfg.costs.spread / 2.0
    slip = cfg.costs.slippage
    upl = cfg.instrument.usd_per_price_per_lot
    stop_dist_eff = tr.risk_usd / (tr.lots * upl) if tr.lots else 0.0
    target = cfg.risk.fixed_r_target * stop_dist_eff

    long = tr.side == Side.LONG.value
    if long:
        tp_market = tr.entry_price + target + hs + slip
    else:
        tp_market = tr.entry_price - target - hs - slip
    sl_market = tr.stop_loss

    exit_eff = None
    reason = "EOD"
    for j in range(tr.entry_index + 1, len(m5)):
        low = float(m5.iloc[j]["low"])
        high = float(m5.iloc[j]["high"])
        hit_sl = low <= sl_market if long else high >= sl_market
        hit_tp = high >= tp_market if long else low <= tp_market
        if hit_sl and hit_tp:
            market, reason = (sl_market, "SL") if cfg.costs.pessimistic_intrabar_fills else (tp_market, "TP")
        elif hit_sl:
            market, reason = sl_market, "SL"
        elif hit_tp:
            market, reason = tp_market, "TP"
        else:
            continue
        exit_eff = (market - hs - slip) if long else (market + hs + slip)
        break

    if exit_eff is None:
        market = float(m5.iloc[-1]["close"])
        exit_eff = (market - hs - slip) if long else (market + hs + slip)
        reason = "EOD"

    pnl = ((exit_eff - tr.entry_price) if long else (tr.entry_price - exit_eff)) * tr.lots * upl
    tr.shadow_exit_price = exit_eff
    tr.shadow_pnl = pnl
    tr.shadow_r_multiple = pnl / tr.risk_usd if tr.risk_usd else 0.0
    tr.shadow_exit_reason = reason
