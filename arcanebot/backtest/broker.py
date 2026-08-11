"""Phase 4 — Backtest broker (simulated ExecutionBackend).

Realistic, bar-by-bar fills — no vectorised shortcuts. Models:
  * spread + slippage on every entry and exit (configurable);
  * limit fills only on a later bar than the one that placed them (no same-bar
    look-ahead);
  * pessimistic intrabar resolution (SL assumed hit before TP when a bar spans
    both);
  * % risk position sizing on current equity;
  * one open position at a time and a hard daily loss cap that halts new entries
    for the rest of the UTC day.

Prices in the incoming candles are treated as mid; costs push entries/exits to
the worse side so results lean conservative.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from config import Config, DEFAULT_CONFIG
from arcanebot.execution.base import (
    ExecutionBackend,
    OrderRequest,
    OrderType,
    Position,
    Side,
)


@dataclass
class Trade:
    side: str
    entry_time: pd.Timestamp
    entry_index: int
    entry_price: float          # effective (cost-adjusted)
    exit_time: pd.Timestamp
    exit_price: float           # effective
    lots: float
    stop_loss: float            # market level
    take_profit: float          # market level
    risk_usd: float
    pnl: float
    r_multiple: float
    exit_reason: str            # "SL" | "TP" | "EOD" | ...
    session: str
    reason: str
    # Fixed-R shadow (filled in by the loop for comparison).
    shadow_exit_price: float | None = None
    shadow_pnl: float | None = None
    shadow_r_multiple: float | None = None
    shadow_exit_reason: str | None = None


class BacktestBroker(ExecutionBackend):
    def __init__(self, cfg: Config = DEFAULT_CONFIG):
        self.cfg = cfg
        self._equity = cfg.risk.starting_equity
        self._position: Position | None = None
        self._pending: OrderRequest | None = None
        # extra state attached to the live position (not in the shared dataclass)
        self._pos_extra: dict = {}
        self.trades: list[Trade] = []
        # daily loss cap tracking
        self._day: object = None
        self._day_start_equity = self._equity
        self.daily_stopped = False

    # ---- ExecutionBackend interface ---------------------------------- #
    @property
    def open_position(self) -> Position | None:
        return self._position

    def equity(self) -> float:
        return self._equity

    def submit(self, order: OrderRequest) -> None:
        if self._position is not None or self._pending is not None or self.daily_stopped:
            return
        self._pending = order

    def cancel_pending(self, reason: str) -> None:
        self._pending = None

    # ---- convenience ------------------------------------------------- #
    def view(self):
        from arcanebot.signals.engine import BrokerView
        return BrokerView(
            has_position=self._position is not None,
            has_pending=self._pending is not None,
            daily_stopped=self.daily_stopped,
        )

    @property
    def half_spread(self) -> float:
        return self.cfg.costs.spread / 2.0

    @property
    def slip(self) -> float:
        return self.cfg.costs.slippage

    def _upl(self) -> float:
        return self.cfg.instrument.usd_per_price_per_lot

    # ---- per-bar processing ------------------------------------------ #
    def process_bar(self, bar, i: int, t: pd.Timestamp) -> list[str]:
        """Fill/exit against bar ``i``. Returns event tags for the loop."""
        events: list[str] = []
        self._roll_day(t)

        if self._position is not None:
            if self._resolve_position(bar, t):
                events.append("closed")
                if self.daily_stopped and self._pending is not None:
                    self._pending = None
                    events.append("cancelled_daily")

        # A position may have just closed; a pending can then fill on a LATER bar
        # than it was created (created_at is a close time <= this bar's open).
        if self._position is None and self._pending is not None:
            if self._try_fill(bar, i, t):
                events.append("filled")

        return events

    def _roll_day(self, t: pd.Timestamp) -> None:
        day = t.normalize()
        if day != self._day:
            self._day = day
            self._day_start_equity = self._equity
            self.daily_stopped = False

    def _try_fill(self, bar, i: int, t: pd.Timestamp) -> bool:
        o = self._pending
        # Don't fill before the order exists. created_at is the MSS bar's CLOSE
        # time, which equals the NEXT bar's open time — so the next bar (t ==
        # created_at) is the earliest fillable bar; anything earlier is skipped.
        if t < o.created_at:
            return False
        low, high = float(bar["low"]), float(bar["high"])
        if o.side is Side.LONG:
            if low <= o.entry_price:
                fill = o.entry_price + self.half_spread + self.slip
                self._open(o, fill, i, t)
                return True
        else:
            if high >= o.entry_price:
                fill = o.entry_price - self.half_spread - self.slip
                self._open(o, fill, i, t)
                return True
        return False

    def _open(self, o: OrderRequest, fill_price: float, i: int, t: pd.Timestamp) -> None:
        # Effective stop for sizing (costs widen the true risk).
        if o.side is Side.LONG:
            sl_eff = o.stop_loss - self.half_spread - self.slip
            stop_dist = fill_price - sl_eff
        else:
            sl_eff = o.stop_loss + self.half_spread + self.slip
            stop_dist = sl_eff - fill_price
        stop_dist = max(stop_dist, 1e-9)

        risk_usd = self._equity * self.cfg.risk.risk_per_trade_pct / 100.0
        raw_lots = risk_usd / (stop_dist * self._upl())
        lots = self._round_lots(raw_lots)

        self._position = Position(
            side=o.side, entry_price=fill_price, stop_loss=o.stop_loss,
            take_profit=o.take_profit, lots=lots, opened_at=t, reason=o.reason,
        )
        # Breakeven lever: after price runs breakeven_at_r in our favour we move
        # the working stop to entry. Computed here; applied on the bar AFTER the
        # trigger (pessimistic — no intrabar "was I at BE or SL first?" guess).
        be_r = self.cfg.risk.breakeven_at_r
        if be_r is not None:
            be_trigger = (fill_price + be_r * stop_dist) if o.side is Side.LONG \
                else (fill_price - be_r * stop_dist)
        else:
            be_trigger = None

        self._pos_extra = {
            "entry_index": i,
            "risk_usd": stop_dist * lots * self._upl(),
            "stop_dist": stop_dist,
            "created": o,
            "stop": o.stop_loss,        # mutable working stop (market level)
            "be_trigger": be_trigger,
            "be_done": False,
        }

        # Partial-TP lever.
        ptp_r = self.cfg.risk.partial_tp_r
        if ptp_r is not None:
            self._pos_extra["partial_trigger"] = (fill_price + ptp_r * stop_dist) \
                if o.side is Side.LONG else (fill_price - ptp_r * stop_dist)
        else:
            self._pos_extra["partial_trigger"] = None
        self._pos_extra["partial_done"] = False
        self._pos_extra["remaining_lots"] = lots
        self._pos_extra["realized_partial"] = 0.0
        self._pending = None

    def _round_lots(self, lots: float) -> float:
        step = self.cfg.instrument.lot_step
        n = max(int(lots / step), 0)
        return max(n * step, self.cfg.instrument.min_lot)

    def _resolve_position(self, bar, t: pd.Timestamp) -> bool:
        p = self._position
        low, high = float(bar["low"]), float(bar["high"])
        stop = self._pos_extra["stop"]      # working stop (may be at breakeven)
        hit_sl = low <= stop if p.side is Side.LONG else high >= stop
        hit_tp = high >= p.take_profit if p.side is Side.LONG else low <= p.take_profit

        exit_market = None
        reason = None
        if hit_sl and hit_tp:
            # Pessimistic: assume the stop went first.
            if self.cfg.costs.pessimistic_intrabar_fills:
                exit_market, reason = stop, "SL"
            else:
                exit_market, reason = p.take_profit, "TP"
        elif hit_sl:
            exit_market, reason = stop, "SL"
        elif hit_tp:
            exit_market, reason = p.take_profit, "TP"

        if exit_market is not None:
            self._close(exit_market, reason, t)
            return True

        # Partial take-profit: on a bar that reaches the partial level WITHOUT
        # first hitting the stop, bank part of the position and (optionally) move
        # the runner's stop to breakeven. Pessimistic: SL was checked first above.
        ptp = self._pos_extra["partial_trigger"]
        if ptp is not None and not self._pos_extra["partial_done"]:
            reached = high >= ptp if p.side is Side.LONG else low <= ptp
            if reached:
                self._take_partial(ptp, t)

        # Arm breakeven for subsequent bars if the trigger printed.
        be = self._pos_extra["be_trigger"]
        if be is not None and not self._pos_extra["be_done"]:
            reached = high >= be if p.side is Side.LONG else low <= be
            if reached:
                self._pos_extra["stop"] = p.entry_price   # move stop to entry
                self._pos_extra["be_done"] = True
        return False

    def _take_partial(self, level_market: float, t: pd.Timestamp) -> None:
        p = self._position
        part_lots = p.lots * self.cfg.risk.partial_tp_frac
        if p.side is Side.LONG:
            level_eff = level_market - self.half_spread - self.slip
            pnl = (level_eff - p.entry_price) * part_lots * self._upl()
        else:
            level_eff = level_market + self.half_spread + self.slip
            pnl = (p.entry_price - level_eff) * part_lots * self._upl()
        self._equity += pnl
        self._pos_extra["realized_partial"] += pnl
        self._pos_extra["remaining_lots"] -= part_lots
        self._pos_extra["partial_done"] = True
        if self.cfg.risk.partial_move_be:
            self._pos_extra["stop"] = p.entry_price
            self._pos_extra["be_done"] = True

    def _close(self, exit_market: float, reason: str, t: pd.Timestamp) -> None:
        p = self._position
        rem_lots = self._pos_extra.get("remaining_lots", p.lots)
        if p.side is Side.LONG:
            exit_eff = exit_market - self.half_spread - self.slip
            runner_pnl = (exit_eff - p.entry_price) * rem_lots * self._upl()
        else:
            exit_eff = exit_market + self.half_spread + self.slip
            runner_pnl = (p.entry_price - exit_eff) * rem_lots * self._upl()

        partial_taken = self._pos_extra.get("partial_done", False)
        pnl = runner_pnl + self._pos_extra.get("realized_partial", 0.0)
        self._equity += runner_pnl   # partial pnl already added when banked
        risk_usd = self._pos_extra["risk_usd"]
        r_mult = pnl / risk_usd if risk_usd else 0.0

        self.trades.append(Trade(
            side=p.side.value, entry_time=p.opened_at,
            entry_index=self._pos_extra["entry_index"], entry_price=p.entry_price,
            exit_time=t, exit_price=exit_eff, lots=p.lots, stop_loss=p.stop_loss,
            take_profit=p.take_profit, risk_usd=risk_usd, pnl=pnl, r_multiple=r_mult,
            exit_reason=(reason + "+P") if partial_taken else reason,
            session=_session_of(p.reason), reason=p.reason,
        ))
        self._position = None
        self._pos_extra = {}

        # Daily loss cap.
        dd = self._equity - self._day_start_equity
        limit = -self.cfg.risk.daily_loss_limit_pct / 100.0 * self._day_start_equity
        if dd <= limit:
            self.daily_stopped = True

    def close_open_at_end(self, m5: pd.DataFrame) -> None:
        """Mark any still-open position out at the final close (for stats)."""
        if self._position is None:
            return
        last_close = float(m5.iloc[-1]["close"])
        self._close(last_close, "EOD", m5.index[-1])


def _session_of(reason: str) -> str:
    for tag in ("london", "new_york_am"):
        if f"session={tag}" in reason:
            return tag
    return "unknown"
