"""Phase 4 tests — backtest loop, broker fills, and accounting invariants."""

import pandas as pd

from config import DEFAULT_CONFIG
from arcanebot.backtest.engine import run_backtest
from arcanebot.data import resample
from arcanebot.structure.sessions import active_entry_session
from tests.test_signal_engine import _fixture


def test_long_fixture_fills_and_wins(make_frame):
    df = _fixture(make_frame)
    m15 = resample(df, "15min")
    result = run_backtest(df, m15, DEFAULT_CONFIG)

    assert len(result.trades) == 1
    tr = result.trades[0]
    assert tr.side == "long"
    assert tr.exit_reason == "TP"
    assert tr.r_multiple > 0
    # A fixed-R shadow was recorded for comparison.
    assert tr.shadow_r_multiple is not None


def test_sl_exits_are_exactly_minus_one_r(make_frame):
    # Run over synthetic data and assert every stop-out is exactly -1R and every
    # entry sits inside a kill zone (core accounting / gating invariants).
    from arcanebot.data import load_candles
    m5 = load_candles("data/sample_xauusd_m5.csv", "5min")
    m15 = resample(m5, "15min")
    result = run_backtest(m5, m15, DEFAULT_CONFIG)
    assert len(result.trades) > 0

    for tr in result.trades:
        if tr.exit_reason == "SL":
            assert abs(tr.r_multiple + 1.0) < 1e-6
        # Entry within a kill zone.
        entry_close = tr.entry_time  # opened_at is a bar close time
        assert active_entry_session(entry_close) is not None
        # Directional sanity: long SL below entry, TP above (mirror for short).
        if tr.side == "long":
            assert tr.stop_loss < tr.entry_price < tr.take_profit
        else:
            assert tr.take_profit < tr.entry_price < tr.stop_loss


def test_daily_loss_cap_halts_entries(make_frame):
    # Force a tiny daily loss cap and a big risk so one loss stops the day.
    from dataclasses import replace
    cfg = DEFAULT_CONFIG
    cfg = replace(cfg, risk=replace(cfg.risk, daily_loss_limit_pct=0.1, risk_per_trade_pct=0.5))
    from arcanebot.data import load_candles
    m5 = load_candles("data/sample_xauusd_m5.csv", "5min")
    m15 = resample(m5, "15min")
    result = run_backtest(m5, m15, cfg)
    # At most one losing trade per day should be possible once the cap is hit.
    df = pd.DataFrame([t.__dict__ for t in result.trades])
    if not df.empty:
        df["day"] = pd.to_datetime(df["entry_time"]).dt.normalize()
        losers_per_day = df[df["pnl"] < 0].groupby("day").size()
        assert (losers_per_day <= 1).all()
