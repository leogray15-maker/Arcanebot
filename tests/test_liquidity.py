"""Phase 2 tests — liquidity pool assembly, clustering, lookups, no-look-ahead."""

import pandas as pd

from arcanebot.structure.liquidity import (
    build_liquidity,
    cluster_equal_levels,
    levels_known_at,
    nearest_buyside_above,
    nearest_sellside_below,
)
from arcanebot.structure.sessions import DayRange, SessionRange
from arcanebot.structure.swings import Swing


def _ts(s):
    return pd.Timestamp(s, tz="UTC")


def _swing(kind, price, t, ct):
    return Swing(kind, 0, _ts(t), price, 0, _ts(ct))


def test_build_liquidity_from_all_sources():
    swings = [
        _swing("high", 2100.0, "2024-01-02 07:00", "2024-01-02 07:10"),
        _swing("low", 2050.0, "2024-01-02 07:20", "2024-01-02 07:30"),
    ]
    sessions = [
        SessionRange("asian", None, _ts("2024-01-02 00:00"), _ts("2024-01-02 07:00"),
                     high=2110.0, low=2040.0,
                     high_time=_ts("2024-01-02 03:00"),
                     low_time=_ts("2024-01-02 05:00"),
                     known_from=_ts("2024-01-02 07:00")),
    ]
    days = [DayRange(None, high=2120.0, low=2030.0, known_from=_ts("2024-01-02 00:00"))]

    levels = build_liquidity(swings, sessions, days)
    buyside = sorted(lv.price for lv in levels if lv.side == "buyside")
    sellside = sorted(lv.price for lv in levels if lv.side == "sellside")
    assert buyside == [2100.0, 2110.0, 2120.0]
    assert sellside == [2030.0, 2040.0, 2050.0]
    sources = {lv.source for lv in levels}
    assert "fractal" in sources
    assert "session:asian" in sources
    assert "prior_day" in sources


def test_levels_known_at_filters_by_time():
    swings = [
        _swing("high", 2100.0, "2024-01-02 07:00", "2024-01-02 07:10"),
    ]
    levels = build_liquidity(swings, [], [])
    # Not known before confirmation.
    assert levels_known_at(levels, _ts("2024-01-02 07:05")) == []
    assert len(levels_known_at(levels, _ts("2024-01-02 07:10"))) == 1


def test_cluster_equal_highs():
    # Three highs within 15c tolerance -> one buyside cluster at the extreme.
    swings = [
        _swing("high", 2100.00, "2024-01-02 07:00", "2024-01-02 07:10"),
        _swing("high", 2100.10, "2024-01-02 07:30", "2024-01-02 07:40"),
        _swing("high", 2100.05, "2024-01-02 08:00", "2024-01-02 08:10"),
        _swing("high", 2200.00, "2024-01-02 08:30", "2024-01-02 08:40"),
    ]
    levels = build_liquidity(swings, [], [])
    clustered = cluster_equal_levels(levels, tolerance=0.15)
    buyside = sorted((lv.price, lv.count) for lv in clustered if lv.side == "buyside")
    # The three ~2100 highs merge (extreme = 2100.10, count 3); 2200 stays alone.
    assert buyside == [(2100.10, 3), (2200.00, 1)]
    # Cluster is known from the EARLIEST member's confirmation.
    merged = [lv for lv in clustered if lv.count == 3][0]
    assert merged.known_from == _ts("2024-01-02 07:10")


def test_nearest_level_lookups():
    swings = [
        _swing("low", 2050.0, "2024-01-02 07:00", "2024-01-02 07:10"),
        _swing("low", 2070.0, "2024-01-02 07:20", "2024-01-02 07:30"),
        _swing("high", 2100.0, "2024-01-02 07:40", "2024-01-02 07:50"),
        _swing("high", 2130.0, "2024-01-02 08:00", "2024-01-02 08:10"),
    ]
    levels = build_liquidity(swings, [], [])
    price = 2080.0
    sell = nearest_sellside_below(levels, price)
    buy = nearest_buyside_above(levels, price)
    assert sell.price == 2070.0   # nearest low below
    assert buy.price == 2100.0    # nearest high above
