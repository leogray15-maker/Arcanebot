"""Phase 5 — The look-ahead test.

The definitive check: a decision made at bar ``i`` must not depend on any bar
after ``i``. We prove it by *truncation invariance* — running the full
engine+broker on data cut off at bar T must reproduce, byte for byte, the exact
sequence of decisions the full-length run produced for bars 0..T. If any
indicator, liquidity level, or signal peeked forward, cutting the future off
would change an earlier decision and this test would fail.

This is stronger than inspecting individual reads: it exercises the entire
pipeline (structure precompute, known_from filtering, swing confirmation, the
state machine, and broker fills) and asserts the whole thing is causal.
"""

import pandas as pd

from config import DEFAULT_CONFIG
from arcanebot.backtest.broker import BacktestBroker
from arcanebot.data import load_candles, resample
from arcanebot.signals.engine import SignalEngine


def _drive(m5: pd.DataFrame, m15: pd.DataFrame, cfg=DEFAULT_CONFIG):
    """Run engine+broker exactly like the backtest, recording each bar's decision."""
    engine = SignalEngine(cfg)
    engine.prepare(m5, m15)
    broker = BacktestBroker(cfg)
    decisions = []
    for i in range(len(m5)):
        bar = m5.iloc[i]
        t = m5.index[i]
        for e in broker.process_bar(bar, i, t):
            if e == "filled":
                engine.notify_filled()
            elif e == "cancelled_daily":
                engine.notify_cancelled()
        dec = engine.evaluate(i, broker.view())
        rec = (
            dec.submit.side.value if dec.submit else None,
            round(dec.submit.entry_price, 4) if dec.submit else None,
            round(dec.submit.stop_loss, 4) if dec.submit else None,
            round(dec.submit.take_profit, 4) if dec.submit else None,
            dec.cancel_reason,
        )
        decisions.append(rec)
        if dec.cancel_reason:
            broker.cancel_pending(dec.cancel_reason)
            engine.notify_cancelled()
        if dec.submit is not None:
            broker.submit(dec.submit)
    return decisions


# A bounded slice keeps the O(n^2) truncation sweep fast while still spanning
# several full setups. Truncation invariance is scale-free — a leak shows up on
# any window that contains a signal.
SLICE = 1300


def _m5():
    return load_candles("data/sample_xauusd_m5.csv", "5min").iloc[:SLICE]


def test_truncation_invariance():
    m5 = _m5()
    full = _drive(m5, resample(m5, "15min"))

    submit_bars = [i for i, d in enumerate(full) if d[0] is not None]
    assert submit_bars, "fixture should produce at least one signal to test"

    # Check a bar or two after each submit (the strictest points) plus a couple
    # of generic offsets.
    checkpoints = set()
    for sb in submit_bars:
        checkpoints.update({sb + 1, sb + 2})
    checkpoints.update({SLICE // 2, SLICE - 1})
    checkpoints = sorted(c for c in checkpoints if 10 <= c < len(m5))

    for T in checkpoints:
        trunc_m5 = m5.iloc[: T + 1]
        trunc = _drive(trunc_m5, resample(trunc_m5, "15min"))
        assert trunc == full[: T + 1], (
            f"decisions diverged when data truncated at bar {T} — "
            f"the engine read a future bar"
        )


def test_partial_htf_bar_not_used_early():
    # A deliberately partial final M15 bar must not change earlier decisions:
    # cut the M5 data mid-M15-bar and confirm the prefix is unchanged.
    m5 = _m5()
    full = _drive(m5, resample(m5, "15min"))
    T = SLICE - 2  # not a multiple of 3 -> final M15 bar is partial
    trunc_m5 = m5.iloc[: T + 1]
    trunc = _drive(trunc_m5, resample(trunc_m5, "15min"))
    assert trunc == full[: T + 1]
