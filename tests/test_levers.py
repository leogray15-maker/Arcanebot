"""Tests — the profitability levers: blackout, HTF trend filter, partial TP."""

from dataclasses import replace

import pandas as pd

from config import DEFAULT_CONFIG
from arcanebot.backtest.broker import BacktestBroker
from arcanebot.backtest.engine import run_backtest
from arcanebot.data import resample
from arcanebot.execution.base import OrderRequest, OrderType, Side
from arcanebot.signals.engine import SignalEngine
from tests.test_signal_engine import _fixture, _run_engine


# --------------------------------------------------------------- blackout ----
def test_blackout_blocks_entries(make_frame):
    df = _fixture(make_frame)
    # Blackout the whole London window -> the fixture's long can't arm.
    cfg = replace(DEFAULT_CONFIG,
                  session=replace(DEFAULT_CONFIG.session, blackout_windows=((7, 0, 10, 0),)))
    m15 = resample(df, "15min")
    eng = SignalEngine(cfg)
    eng.prepare(df, m15)
    from arcanebot.signals.engine import BrokerView
    flat = BrokerView(False, False, False)
    submits = [eng.evaluate(i, flat).submit for i in range(len(df))]
    assert all(s is None for s in submits)

    # Sanity: without the blackout the same fixture DOES produce a signal.
    assert len(_run_engine(df)) == 1


# ------------------------------------------------------------ HTF filter ----
def test_htf_trend_gate():
    # Build a steady uptrend so the HTF EMA sits below price -> long bias.
    idx = pd.date_range("2024-01-02 00:00", periods=200, freq="5min", tz="UTC")
    close = pd.Series(range(200), index=idx) * 0.5 + 2000.0
    df = pd.DataFrame({
        "open": close.shift(1).fillna(close.iloc[0]),
        "high": close + 0.5, "low": close - 0.5, "close": close, "volume": 100,
    }, index=idx)
    df.index.name = "timestamp"

    cfg = replace(DEFAULT_CONFIG,
                  signal=replace(DEFAULT_CONFIG.signal, htf_trend_filter=True,
                                 htf_trend_timeframe="60min", htf_trend_ema=3))
    eng = SignalEngine(cfg)
    eng.prepare(df, resample(df, "15min"))
    t = eng._killzone_end  # noqa - just to keep ref; use a late close time
    late = pd.Timestamp("2024-01-02 10:00", tz="UTC")
    assert eng._trend_allows(Side.LONG, late) is True
    assert eng._trend_allows(Side.SHORT, late) is False


# ------------------------------------------------------------ partial TP ----
def _bar(o, h, l, c):
    return pd.Series({"open": o, "high": h, "low": l, "close": c, "volume": 100})


def test_partial_tp_banks_and_moves_to_breakeven():
    cfg = replace(DEFAULT_CONFIG, costs=replace(DEFAULT_CONFIG.costs, spread=0.0, slippage=0.0))
    cfg = replace(cfg, risk=replace(cfg.risk, partial_tp_r=1.0, partial_tp_frac=0.5,
                                    partial_move_be=True, breakeven_at_r=None))
    t = [pd.Timestamp("2024-01-02 07:00", tz="UTC") + pd.Timedelta(minutes=5 * k) for k in range(4)]
    broker = BacktestBroker(cfg)
    broker.submit(OrderRequest(Side.LONG, OrderType.LIMIT, 100.0, 98.0, 110.0, 0.0,
                               t[0], None, "test session=london"))

    broker.process_bar(_bar(100, 101, 100, 100.5), 1, t[1])       # fill at 100
    broker.process_bar(_bar(100.5, 103, 100.4, 102.5), 2, t[2])   # +1R -> bank 50%, BE
    broker.process_bar(_bar(102, 102.5, 99.5, 100.0), 3, t[3])    # back to entry -> BE stop

    assert len(broker.trades) == 1
    tr = broker.trades[0]
    assert tr.exit_reason.endswith("+P")           # partial was taken
    assert 0.4 < tr.r_multiple < 0.6               # ~+0.5R (half at +1R, runner ~BE)
