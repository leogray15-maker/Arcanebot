"""Phase 1 tests — data layer.

Fixtures are tiny and hand-verified so the expected answers are known exactly.
"""

from pathlib import Path

import pandas as pd
import pytest

from arcanebot.data import (
    DataValidationError,
    detect_gaps,
    load_candles,
    resample,
    validate_ohlc,
)
from arcanebot.data.loader import _frame_from_raw
from config import DEFAULT_CONFIG

FIXTURE = Path(__file__).parent / "fixtures" / "tiny_m5.csv"
DCFG = DEFAULT_CONFIG.data


def _raw(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close", "volume"])


# --------------------------------------------------------------------------- #
# Loading & validation
# --------------------------------------------------------------------------- #
def test_load_fixture_ok():
    df = load_candles(str(FIXTURE), "5min")
    assert list(df.columns) == ["open", "high", "low", "close", "volume"]
    assert len(df) == 6
    assert str(df.index.tz) == "UTC"
    assert df.index.is_monotonic_increasing
    # Spot-check a value.
    assert df.loc["2024-01-02 07:10:00+00:00", "high"] == 2052.50


def test_missing_column_raises():
    raw = _raw([{"timestamp": "2024-01-02 07:00:00", "open": 1, "high": 2,
                 "low": 1, "close": 2, "volume": 1}]).drop(columns=["volume"])
    with pytest.raises(DataValidationError, match="missing required columns"):
        _frame_from_raw(raw, "5min", DCFG, validate=True)


def test_high_below_low_raises():
    raw = _raw([{"timestamp": "2024-01-02 07:00:00", "open": 2050, "high": 2049,
                 "low": 2051, "close": 2050, "volume": 10}])
    with pytest.raises(DataValidationError, match="high < low"):
        _frame_from_raw(raw, "5min", DCFG, validate=True)


def test_high_below_close_raises():
    raw = _raw([{"timestamp": "2024-01-02 07:00:00", "open": 2050, "high": 2050.5,
                 "low": 2049, "close": 2051, "volume": 10}])
    with pytest.raises(DataValidationError, match="high is below"):
        _frame_from_raw(raw, "5min", DCFG, validate=True)


def test_nan_value_raises():
    raw = _raw([{"timestamp": "2024-01-02 07:00:00", "open": 2050, "high": "x",
                 "low": 2049, "close": 2050, "volume": 10}])
    with pytest.raises(DataValidationError, match="NaN"):
        _frame_from_raw(raw, "5min", DCFG, validate=True)


def test_duplicate_timestamp_raises():
    raw = _raw([
        {"timestamp": "2024-01-02 07:00:00", "open": 2050, "high": 2051, "low": 2049, "close": 2050, "volume": 10},
        {"timestamp": "2024-01-02 07:00:00", "open": 2050, "high": 2051, "low": 2049, "close": 2050, "volume": 10},
    ])
    with pytest.raises(DataValidationError, match="duplicate"):
        _frame_from_raw(raw, "5min", DCFG, validate=True)


def test_unsorted_input_is_sorted_not_rejected():
    raw = _raw([
        {"timestamp": "2024-01-02 07:05:00", "open": 2050, "high": 2051, "low": 2049, "close": 2050, "volume": 10},
        {"timestamp": "2024-01-02 07:00:00", "open": 2050, "high": 2051, "low": 2049, "close": 2050, "volume": 10},
    ])
    df = _frame_from_raw(raw, "5min", DCFG, validate=True)
    assert list(df.index) == sorted(df.index)


def test_empty_frame_raises():
    df = load_candles(str(FIXTURE), "5min").iloc[0:0]
    with pytest.raises(DataValidationError, match="empty"):
        validate_ohlc(df)


# --------------------------------------------------------------------------- #
# Resampling M5 -> M15 (hand-verified)
# --------------------------------------------------------------------------- #
def test_resample_m5_to_m15_values():
    m5 = load_candles(str(FIXTURE), "5min")
    m15 = resample(m5, "15min")

    assert len(m15) == 2
    b0 = m15.iloc[0]
    assert (b0["open"], b0["high"], b0["low"], b0["close"], b0["volume"]) == (
        2050.00, 2052.50, 2049.50, 2050.40, 470.0
    )
    b1 = m15.iloc[1]
    assert (b1["open"], b1["high"], b1["low"], b1["close"], b1["volume"]) == (
        2050.40, 2050.90, 2046.90, 2048.10, 720.0
    )
    # Left-labelled bins.
    assert str(m15.index[0]) == "2024-01-02 07:00:00+00:00"
    assert str(m15.index[1]) == "2024-01-02 07:15:00+00:00"


def test_resample_drops_weekend_bins():
    # Two M5 bars far apart (weekend). Only two non-empty M15 bins should exist.
    raw = _raw([
        {"timestamp": "2024-01-05 20:55:00", "open": 2050, "high": 2051, "low": 2049, "close": 2050, "volume": 10},
        {"timestamp": "2024-01-07 22:05:00", "open": 2050, "high": 2051, "low": 2049, "close": 2050, "volume": 10},
    ])
    df = _frame_from_raw(raw, "5min", DCFG, validate=True)
    m15 = resample(df, "15min")
    assert len(m15) == 2  # no synthetic NaN bins for the closed weekend


# --------------------------------------------------------------------------- #
# Gap detection
# --------------------------------------------------------------------------- #
def test_no_gaps_on_continuous_series():
    m5 = load_candles(str(FIXTURE), "5min")
    assert detect_gaps(m5, "5min") == []


def test_intra_session_gap_flagged():
    raw = _raw([
        {"timestamp": "2024-01-02 07:00:00", "open": 2050, "high": 2051, "low": 2049, "close": 2050, "volume": 10},
        {"timestamp": "2024-01-02 07:20:00", "open": 2050, "high": 2051, "low": 2049, "close": 2050, "volume": 10},
    ])
    df = _frame_from_raw(raw, "5min", DCFG, validate=True)
    gaps = detect_gaps(df, "5min")
    assert len(gaps) == 1
    assert gaps[0].missing_bars == 3          # 07:05, 07:10, 07:15 missing
    assert gaps[0].is_session_break is False


def test_weekend_gap_marked_session_break():
    raw = _raw([
        {"timestamp": "2024-01-05 21:55:00", "open": 2050, "high": 2051, "low": 2049, "close": 2050, "volume": 10},
        {"timestamp": "2024-01-07 22:00:00", "open": 2050, "high": 2051, "low": 2049, "close": 2050, "volume": 10},
    ])
    df = _frame_from_raw(raw, "5min", DCFG, validate=True)
    gaps = detect_gaps(df, "5min")
    assert len(gaps) == 1
    assert gaps[0].is_session_break is True
