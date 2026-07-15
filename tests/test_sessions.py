"""Phase 2 tests — session windows and session-range liquidity."""

import pandas as pd

from arcanebot.structure.sessions import (
    active_entry_session,
    compute_prior_day_levels,
    compute_session_ranges,
    in_window,
)
from config import DEFAULT_CONFIG

SCFG = DEFAULT_CONFIG.session


def _ts(s):
    return pd.Timestamp(s, tz="UTC")


def test_in_window_boundaries():
    london = SCFG.london  # (7,0,10,0)
    assert in_window(_ts("2024-01-02 07:00"), london) is True   # inclusive start
    assert in_window(_ts("2024-01-02 09:59"), london) is True
    assert in_window(_ts("2024-01-02 10:00"), london) is False  # exclusive end
    assert in_window(_ts("2024-01-02 06:59"), london) is False


def test_active_entry_session():
    assert active_entry_session(_ts("2024-01-02 08:00")) == "london"
    assert active_entry_session(_ts("2024-01-02 13:00")) == "new_york_am"
    assert active_entry_session(_ts("2024-01-02 05:00")) is None  # asian = no entry
    assert active_entry_session(_ts("2024-01-02 11:00")) is None  # between zones


def _session_frame(make_frame):
    # Asian bars 00:00, 03:00, 06:00 ; London bars 07:00, 08:00, 09:00.
    # Asian high = 12 @03:00, low = 4 @03:00.
    # London high = 15 @07:00, low = 7 @08:00.
    rows_times = [
        ("2024-01-02 00:00", (10, 10, 5, 8)),
        ("2024-01-02 03:00", (8, 12, 4, 11)),
        ("2024-01-02 06:00", (11, 11, 6, 9)),
        ("2024-01-02 07:00", (10, 15, 9, 14)),
        ("2024-01-02 08:00", (14, 14, 7, 8)),
        ("2024-01-02 09:00", (8, 13, 9, 12)),
    ]
    frames = [make_frame([ohlc], start=t, freq="1h") for t, ohlc in rows_times]
    return pd.concat(frames)


def test_session_ranges(make_frame):
    df = _session_frame(make_frame)
    ranges = compute_session_ranges(df, SCFG, sessions=("asian", "london"))
    by_name = {r.name: r for r in ranges}

    asian = by_name["asian"]
    assert (asian.high, asian.low) == (12.0, 4.0)
    assert asian.known_from == _ts("2024-01-02 07:00")   # asian window end

    london = by_name["london"]
    assert (london.high, london.low) == (15.0, 7.0)
    assert london.high_time == _ts("2024-01-02 07:00")
    assert london.low_time == _ts("2024-01-02 08:00")
    assert london.known_from == _ts("2024-01-02 10:00")


def test_prior_day_levels(make_frame):
    day1 = make_frame([(10, 20, 5, 15)], start="2024-01-02 08:00", freq="1h")
    day2 = make_frame([(15, 25, 10, 20)], start="2024-01-03 08:00", freq="1h")
    df = pd.concat([day1, day2])

    levels = compute_prior_day_levels(df)
    assert len(levels) == 2
    d1 = levels[0]
    assert (d1.high, d1.low) == (20.0, 5.0)
    # Day-1 levels only usable from the start of day 2.
    assert d1.known_from == _ts("2024-01-03 00:00")
