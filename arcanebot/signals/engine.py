"""Phase 3 — Signal engine.

Codifies the ICT session-liquidity model into a bar-by-bar state machine that
runs on CLOSED M5 candles and reads liquidity/structure from M15. It emits
OrderRequests (with a full reason trail) that the backtest broker or the live
adapter execute through the shared ExecutionBackend interface.

This is *our codification* of the written spec — every ambiguous phrase was
pinned to a concrete, testable rule (see README "Decisions"). Long setup:

    sweep sellside liquidity  ->  close back above it  ->  price closes above the
    last short-term swing high before the sweep (MSS)  ->  a displacement candle
    in that leg leaves a PD array (FVG / OB / VI)  ->  buy-limit into the deepest
    discount PD array  ->  SL below the sweep low, TP at opposing buyside
    liquidity (fixed-R shadow always recorded).

Short is the exact mirror. No-look-ahead is structural: at bar ``i`` only bars
0..i are ever read, liquidity is filtered by ``known_from <= bar close`` and M5
swings by ``confirmed_index <= i``. The Phase 5 test proves it.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

import pandas as pd

import numpy as np

from config import Config, DEFAULT_CONFIG
from arcanebot.data.loader import bar_close_time, resample
from arcanebot.execution.base import OrderRequest, OrderType, Side
from arcanebot.structure.liquidity import (
    LiquidityLevel,
    build_liquidity,
    cluster_equal_levels,
    levels_known_at,
    nearest_buyside_above,
    nearest_sellside_below,
)
from arcanebot.structure.pd_arrays import (
    PDArray,
    find_fvgs,
    find_order_block,
    find_volume_imbalances,
)
from arcanebot.structure.sessions import (
    active_entry_session,
    compute_prior_day_levels,
    compute_session_ranges,
)
from arcanebot.structure.swings import Swing, find_swings


@dataclass(frozen=True)
class BrokerView:
    """What the engine needs to know about account state each bar."""
    has_position: bool
    has_pending: bool
    daily_stopped: bool


@dataclass
class Setup:
    """Live state of a setup being tracked (one at a time)."""
    side: Side
    phase: str                # "await_mss" | "pending"
    swept_level: float
    sweep_extreme: float      # sweep low (long) / high (short)
    sweep_index: int
    mss_level: float          # swing high (long) / low (short) to break
    expires_at: pd.Timestamp  # kill-zone end
    session: str
    reason_trail: list


@dataclass
class Decision:
    """Engine output for one bar: at most one submit and/or one cancel."""
    submit: OrderRequest | None = None
    cancel_reason: str | None = None
    logs: list = None

    def __post_init__(self):
        if self.logs is None:
            self.logs = []


class SignalEngine:
    def __init__(self, cfg: Config = DEFAULT_CONFIG):
        self.cfg = cfg
        self.active: Setup | None = None
        # Populated by prepare().
        self.m5: pd.DataFrame | None = None
        self.m5_swings: list[Swing] = []
        self.levels: list[LiquidityLevel] = []

    # ------------------------------------------------------------------ #
    # Setup (one-time, deterministic; filtered per-bar by known_from)
    # ------------------------------------------------------------------ #
    def prepare(self, m5: pd.DataFrame, m15: pd.DataFrame) -> None:
        self.m5 = m5
        n = self.cfg.structure.swing_n
        self.m5_swings = find_swings(m5, n=n)

        # M15 swings become liquidity, but at M5 resolution they are only known
        # once the confirming M15 bar has CLOSED — bump known_from by one M15.
        m15_swings = find_swings(m15, n=n)
        htf = pd.Timedelta(self.cfg.data.structure_timeframe)
        m15_swings = [replace(s, confirmed_time=s.confirmed_time + htf) for s in m15_swings]

        session_ranges = compute_session_ranges(m5, self.cfg.session)
        day_ranges = compute_prior_day_levels(m5)
        # RAW (unclustered) levels. Clustering must happen AFTER filtering to
        # known levels each bar, else a future level can merge into a
        # past-known cluster and leak its price backward (see Phase 5 test).
        self.levels = build_liquidity(m15_swings, session_ranges, day_ranges, self.cfg.structure)
        self._known_levels: list[LiquidityLevel] = []

        # LEVER — HTF trend bias, computed once and consumed causally by close
        # time. Each HTF bar's EMA is only "known" once that bar has closed.
        self._htf_close_times = None
        self._htf_bias = None
        if self.cfg.signal.htf_trend_filter:
            tf = self.cfg.signal.htf_trend_timeframe
            htf_df = resample(m5, tf, self.cfg.data)
            ema = htf_df["close"].ewm(span=self.cfg.signal.htf_trend_ema, adjust=False).mean()
            bias = (htf_df["close"] > ema).map(lambda up: 1 if up else -1)
            dur = pd.Timedelta(tf)
            # Store tz-naive UTC datetime64 so searchsorted compares cleanly.
            self._htf_close_times = (htf_df.index + dur).tz_localize(None).to_numpy()
            self._htf_bias = bias.to_numpy()

    # ------------------------------------------------------------------ #
    # Per-bar evaluation
    # ------------------------------------------------------------------ #
    def evaluate(self, i: int, broker: BrokerView) -> Decision:
        """Evaluate the closed M5 bar ``i`` and return the engine's decision."""
        dec = Decision()
        bar = self.m5.iloc[i]
        t_open = self.m5.index[i]
        t_close = bar_close_time(t_open, self.cfg.data.entry_timeframe)

        # Causal liquidity: filter to known levels, THEN cluster equal ones.
        self._known_levels = cluster_equal_levels(
            levels_known_at(self.levels, t_close),
            self.cfg.structure.equal_level_tolerance,
        )

        # 1) Manage an existing setup first (invalidation / MSS / expiry).
        if self.active is not None:
            self._manage_active(i, bar, t_close, dec)

        # 2) If flat and inside a kill zone, try to start a fresh setup.
        can_start = (
            self.active is None
            and not broker.has_position
            and not broker.has_pending
            and not broker.daily_stopped
        )
        if can_start and active_entry_session(t_close, self.cfg.session) is not None:
            self._try_start_setup(i, t_close, dec)
            # A setup started this bar may already qualify for MSS on the same
            # reclaim candle — re-check immediately.
            if self.active is not None and self.active.phase == "await_mss":
                self._manage_active(i, bar, t_close, dec)

        return dec

    # ---- broker callbacks (so the engine can free/keep its state) ------ #
    def notify_filled(self) -> None:
        """Pending limit filled — the broker now owns the live position."""
        self.active = None

    def notify_cancelled(self) -> None:
        self.active = None

    # ------------------------------------------------------------------ #
    # Internal: start a setup on a fresh sweep completing at bar i
    # ------------------------------------------------------------------ #
    def _in_blackout(self, t_close: pd.Timestamp) -> bool:
        t = t_close.time()
        for sh, sm, eh, em in self.cfg.session.blackout_windows:
            from datetime import time as _time
            if _time(sh, sm) <= t < _time(eh, em):
                return True
        return False

    def _trend_allows(self, side: Side, t_close: pd.Timestamp) -> bool:
        """HTF trend gate. True when the filter is off or the bias agrees."""
        if not self.cfg.signal.htf_trend_filter or self._htf_bias is None:
            return True
        key = t_close.tz_localize(None).to_datetime64()
        idx = int(np.searchsorted(self._htf_close_times, key, side="right")) - 1
        if idx < 0:
            return False  # no closed HTF bar yet -> no bias, stay out
        bias = self._htf_bias[idx]
        return bool(bias > 0) if side is Side.LONG else bool(bias < 0)

    def _try_start_setup(self, i: int, t_close: pd.Timestamp, dec: Decision) -> None:
        if self._in_blackout(t_close):
            return
        known = self._known_levels
        # Try long (sweep of sellside) then short (sweep of buyside).
        for side in (Side.LONG, Side.SHORT):
            if not self._trend_allows(side, t_close):
                continue
            setup = self._detect_sweep(i, side, known, t_close)
            if setup is not None:
                self.active = setup
                dec.logs.append(
                    f"[{t_close}] {side.value.upper()} sweep of "
                    f"{setup.swept_level:.2f} (extreme {setup.sweep_extreme:.2f}); "
                    f"await MSS over {setup.mss_level:.2f}"
                )
                return

    def _detect_sweep(
        self, i: int, side: Side, known: list[LiquidityLevel], t_close: pd.Timestamp
    ) -> Setup | None:
        cfg = self.cfg
        lows = self.m5["low"].to_numpy()
        highs = self.m5["high"].to_numpy()
        closes = self.m5["close"].to_numpy()

        reentry = cfg.signal.sweep_reentry_bars
        # Poke bars: the reclaim happens at bar i; the poke is bar i itself or up
        # to `reentry` bars earlier.
        for poke in range(i, max(i - reentry, -1) - 1, -1):
            if side is Side.LONG:
                level = nearest_sellside_below(known, closes[poke])
                if level is None:
                    continue
                poked = lows[poke] < level.price
                reclaimed = closes[i] > level.price
                # All bars between poke and i must not have closed above already
                # (poke bar close may be below the level).
                if poked and reclaimed:
                    extreme = float(min(lows[poke:i + 1]))
                    mss = self._pre_sweep_swing(poke, "high", i)
                    if mss is None:
                        continue
                    return Setup(
                        side=side, phase="await_mss", swept_level=level.price,
                        sweep_extreme=extreme, sweep_index=poke, mss_level=mss.price,
                        expires_at=self._killzone_end(t_close),
                        session=active_entry_session(t_close, cfg.session),
                        reason_trail=[
                            f"swept sellside {level.price:.2f} ({level.source})"
                        ],
                    )
            else:
                level = nearest_buyside_above(known, closes[poke])
                if level is None:
                    continue
                poked = highs[poke] > level.price
                reclaimed = closes[i] < level.price
                if poked and reclaimed:
                    extreme = float(max(highs[poke:i + 1]))
                    mss = self._pre_sweep_swing(poke, "low", i)
                    if mss is None:
                        continue
                    return Setup(
                        side=side, phase="await_mss", swept_level=level.price,
                        sweep_extreme=extreme, sweep_index=poke, mss_level=mss.price,
                        expires_at=self._killzone_end(t_close),
                        session=active_entry_session(t_close, cfg.session),
                        reason_trail=[
                            f"swept buyside {level.price:.2f} ({level.source})"
                        ],
                    )
        return None

    def _pre_sweep_swing(self, sweep_index: int, kind: str, known_at: int) -> Swing | None:
        """The most recent M5 swing of ``kind`` before the sweep, known by now."""
        candidates = [
            s for s in self.m5_swings
            if s.kind == kind and s.index < sweep_index and s.confirmed_index <= known_at
        ]
        return max(candidates, key=lambda s: s.index) if candidates else None

    # ------------------------------------------------------------------ #
    # Internal: manage an active setup (MSS confirmation, invalidation)
    # ------------------------------------------------------------------ #
    def _manage_active(self, i: int, bar, t_close: pd.Timestamp, dec: Decision) -> None:
        s = self.active
        close = float(bar["close"])

        # Kill-zone expiry.
        if self.cfg.session.cancel_on_killzone_end and t_close >= s.expires_at:
            if s.phase == "pending":
                dec.cancel_reason = "kill-zone ended before fill"
            dec.logs.append(f"[{t_close}] setup cancelled: kill-zone ended")
            self.active = None
            return

        if s.side is Side.LONG:
            invalidated = close < s.sweep_extreme
        else:
            invalidated = close > s.sweep_extreme
        if invalidated:
            if s.phase == "pending":
                dec.cancel_reason = "closed beyond sweep extreme"
            dec.logs.append(
                f"[{t_close}] setup invalidated: close {close:.2f} beyond "
                f"sweep extreme {s.sweep_extreme:.2f}"
            )
            self.active = None
            return

        if s.phase == "await_mss":
            broke = close > s.mss_level if s.side is Side.LONG else close < s.mss_level
            if broke:
                order = self._build_order(i, s, t_close, dec)
                if order is None:
                    # MSS happened but no valid PD array / entry — abandon.
                    self.active = None
                else:
                    s.phase = "pending"
                    dec.submit = order

    # ------------------------------------------------------------------ #
    # Internal: build the entry order at the chosen PD array
    # ------------------------------------------------------------------ #
    def _build_order(self, i: int, s: Setup, t_close: pd.Timestamp, dec: Decision):
        cfg = self.cfg
        leg_lo, leg_hi = s.sweep_index, i  # displacement leg = sweep..MSS bar
        disp_index = self._find_displacement(leg_lo, leg_hi, s.side)
        if cfg.signal.require_displacement and disp_index is None:
            dec.logs.append(f"[{t_close}] MSS but no displacement candle — skip")
            return None

        pd_array = self._select_pd_array(s, leg_lo, leg_hi, disp_index)
        if pd_array is None:
            dec.logs.append(f"[{t_close}] MSS but no valid PD array — skip")
            return None

        entry = self._entry_price(pd_array, s.side)
        close_now = float(self.m5.iloc[i]["close"])
        # A limit must sit on the correct side of current price.
        if s.side is Side.LONG and not entry < close_now:
            dec.logs.append(f"[{t_close}] PD array above price — skip long limit")
            return None
        if s.side is Side.SHORT and not entry > close_now:
            dec.logs.append(f"[{t_close}] PD array below price — skip short limit")
            return None

        buf = cfg.risk.sl_buffer
        known = self._known_levels
        min_dist = cfg.risk.min_tp_r
        if s.side is Side.LONG:
            sl = s.sweep_extreme - buf
            r = entry - sl
            if r <= 0:
                return None
            # Nearest buyside liquidity that is at least min_tp_r away — closer
            # levels are smaller than the spread and make degenerate targets.
            floor = entry + min_dist * r
            liq = min((lv for lv in known if lv.side == "buyside" and lv.price >= floor),
                      key=lambda lv: lv.price, default=None)
            tp_liq = liq.price if liq else None
            tp_fixed = entry + cfg.risk.fixed_r_target * r
        else:
            sl = s.sweep_extreme + buf
            r = sl - entry
            if r <= 0:
                return None
            ceil = entry - min_dist * r
            liq = max((lv for lv in known if lv.side == "sellside" and lv.price <= ceil),
                      key=lambda lv: lv.price, default=None)
            tp_liq = liq.price if liq else None
            tp_fixed = entry - cfg.risk.fixed_r_target * r

        tp = tp_liq if (cfg.risk.tp_mode == "liquidity" and tp_liq is not None) else tp_fixed

        reason = (
            f"{s.side.value.upper()} | {'; '.join(s.reason_trail)}; "
            f"MSS>{s.mss_level:.2f}; {pd_array.kind} entry {entry:.2f}; "
            f"SL {sl:.2f}; TP {tp:.2f} "
            f"({'liquidity' if tp is tp_liq else 'fixed-R'}); "
            f"R={r:.2f}; session={s.session}"
        )
        dec.logs.append(f"[{t_close}] ARM {reason}")

        return OrderRequest(
            side=s.side,
            order_type=OrderType.LIMIT,
            entry_price=entry,
            stop_loss=sl,
            take_profit=tp,
            lots=0.0,  # broker sizes it
            created_at=t_close,
            expires_at=s.expires_at,
            reason=reason,
        )

    def _find_displacement(self, lo: int, hi: int, side: Side) -> int | None:
        cfg = self.cfg.signal
        opens = self.m5["open"].to_numpy()
        closes = self.m5["close"].to_numpy()
        bodies = abs(closes - opens)
        best = None
        best_body = 0.0
        for k in range(lo, hi + 1):
            start = max(0, k - cfg.displacement_lookback)
            if k - start < 1:
                continue
            avg = bodies[start:k].mean()
            if avg <= 0:
                continue
            directional = (closes[k] > opens[k]) if side is Side.LONG else (closes[k] < opens[k])
            if directional and bodies[k] >= cfg.displacement_body_mult * avg:
                if bodies[k] > best_body:
                    best_body, best = bodies[k], k
        return best

    def _select_pd_array(
        self, s: Setup, lo: int, hi: int, disp_index: int | None
    ) -> PDArray | None:
        cfg = self.cfg
        want_side = "bullish" if s.side is Side.LONG else "bearish"
        leg = self.m5.iloc[lo:hi + 1]

        candidates: list[PDArray] = []
        candidates += [p for p in find_fvgs(leg, side=want_side)]
        candidates += [p for p in find_volume_imbalances(leg, side=want_side)]
        if disp_index is not None:
            ob = find_order_block(self.m5, disp_index, want_side, cfg.structure.ob_zone_mode)
            if ob is not None and lo <= ob.index <= hi:
                candidates.append(ob)

        if not candidates:
            return None

        # Discount/premium filter over the sweep->MSS range.
        rng_lo, rng_hi = s.sweep_extreme, s.mss_level
        mid = (rng_lo + rng_hi) / 2.0
        if cfg.signal.prefer_discount_half:
            if s.side is Side.LONG:
                zone = [p for p in candidates if p.midpoint <= mid]
            else:
                zone = [p for p in candidates if p.midpoint >= mid]
            if zone:
                candidates = zone

        priority = {k: n for n, k in enumerate(cfg.signal.pd_array_priority)}

        def deepness(p: PDArray):
            # Deepest = closest to the sweep extreme.
            d = p.bottom if s.side is Side.LONG else -p.top
            return (d, priority.get(p.kind, 99))

        # Long: smallest bottom is deepest -> min. Short: we negated, so min too.
        return min(candidates, key=deepness)

    def _entry_price(self, p: PDArray, side: Side) -> float:
        edge = self.cfg.signal.entry_edge
        if edge == "mid":
            return p.midpoint
        if side is Side.LONG:
            return p.top if edge == "proximal" else p.bottom
        return p.bottom if edge == "proximal" else p.top

    def _killzone_end(self, t_close: pd.Timestamp) -> pd.Timestamp:
        name = active_entry_session(t_close, self.cfg.session)
        window = getattr(self.cfg.session, name)
        _, _, eh, em = window
        day = t_close.normalize()
        return day + pd.Timedelta(hours=eh, minutes=em)
