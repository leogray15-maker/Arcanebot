"""Shared test helpers."""

import pandas as pd
import pytest


def _make_frame(rows, start="2024-01-02 07:00", freq="5min", tz="UTC"):
    """Build an OHLCV frame from (open, high, low, close[, volume]) tuples.

    Timestamps are generated at ``freq`` from ``start`` (UTC), matching the
    loader's output shape: DatetimeIndex named 'timestamp', float OHLCV columns.
    """
    idx = pd.date_range(start=start, periods=len(rows), freq=freq, tz=tz)
    data = {"open": [], "high": [], "low": [], "close": [], "volume": []}
    for r in rows:
        o, h, l, c = r[0], r[1], r[2], r[3]
        v = r[4] if len(r) > 4 else 100
        data["open"].append(float(o))
        data["high"].append(float(h))
        data["low"].append(float(l))
        data["close"].append(float(c))
        data["volume"].append(float(v))
    df = pd.DataFrame(data, index=idx)
    df.index.name = "timestamp"
    return df


@pytest.fixture
def make_frame():
    return _make_frame
