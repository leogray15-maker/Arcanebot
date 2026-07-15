"""Phase 3 tests — full sweep -> MSS -> PD-array long setup, end to end.

The fixture is engineered so every stage is known by hand:
  * Day 1 leaves a prior-day low 1999.5 and high 2020 as standing liquidity.
  * Day 2 London: an M5 swing high at 2012, a decline that sweeps 1999.5 and
    reclaims, then an up displacement that closes above 2012 (MSS) and leaves a
    bullish FVG at [2002, 2004].
Expected order: buy limit at the FVG top (2004.0), SL below the sweep low
(1998.35), TP at buyside liquidity (2020.0).
"""

import pandas as pd

from config import DEFAULT_CONFIG
from arcanebot.data import resample
from arcanebot.execution.base import Side
from arcanebot.signals.engine import BrokerView, SignalEngine

FLAT = BrokerView(has_position=False, has_pending=False, daily_stopped=False)

DAY1 = [  # (o, h, l, c) — prior-day high 2020, low 1999.5
    (2010, 2020, 1999.5, 2015),
    (2015, 2016, 2010, 2012),
    (2012, 2014, 2008, 2010),
]
DAY2 = [
    (2007, 2008, 2006, 2007),      # 0
    (2007, 2010, 2006, 2009),      # 1
    (2009, 2012, 2008, 2010),      # 2  swing high @2012
    (2010, 2011, 2005, 2006),      # 3
    (2006, 2007, 2002, 2003),      # 4
    (2003, 2004, 2000.5, 2001),    # 5
    (2001, 2002, 1998.5, 2001),    # 6  sweep of 1999.5, reclaim
    (2001, 2006, 2000, 2005),      # 7  rally
    (2005, 2013, 2004, 2012.5),    # 8  displacement up -> MSS; FVG [2002, 2004]
    (2012.5, 2013, 2003.9, 2004.2),# 9  retrace into FVG
    (2004, 2021, 2003, 2020.5),    # 10 run to buyside liquidity
]


def _fixture(make_frame, day2_start="2024-01-02 07:00"):
    d1 = make_frame(DAY1, start="2024-01-01 08:00", freq="5min")
    d2 = make_frame(DAY2, start=day2_start, freq="5min")
    return pd.concat([d1, d2])


def _run_engine(df):
    m15 = resample(df, "15min")
    eng = SignalEngine(DEFAULT_CONFIG)
    eng.prepare(df, m15)
    submits = []
    for i in range(len(df)):
        dec = eng.evaluate(i, FLAT)
        if dec.submit is not None:
            submits.append(dec.submit)
    return submits


def test_long_setup_emits_expected_order(make_frame):
    df = _fixture(make_frame)
    submits = _run_engine(df)

    assert len(submits) == 1
    o = submits[0]
    assert o.side is Side.LONG
    assert round(o.entry_price, 2) == 2004.00      # FVG top (proximal)
    assert round(o.stop_loss, 2) == 1998.35        # sweep low 1998.5 - 0.15 buffer
    assert round(o.take_profit, 2) == 2020.00      # buyside liquidity
    assert "MSS>2012.00" in o.reason
    assert "FVG" in o.reason


def test_no_entry_outside_killzone(make_frame):
    # Same structure but during the Asian window (03:00 UTC) -> no entries.
    df = _fixture(make_frame, day2_start="2024-01-02 03:00")
    submits = _run_engine(df)
    assert submits == []
