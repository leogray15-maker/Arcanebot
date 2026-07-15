"""Phase 1 — Data layer.

Loads and validates XAUUSD candle CSVs, and resamples M5 -> M15.

Timestamp convention (critical for no-look-ahead):
    Each row's ``timestamp`` is the bar's OPEN time. A bar labelled 07:00 on an
    M5 series covers [07:00, 07:05) and is only CLOSED (and therefore usable by
    the signal engine) at 07:05. The data layer never looks forward — it only
    reshapes rows — but downstream engines must respect that a bar becomes
    actionable at open_time + timeframe. Helpers here expose the bar duration so
    that alignment can be done correctly and testably later.

The loader is intentionally strict: bad data is the most common source of
fake-good backtests, so we fail loudly rather than silently coercing.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from config import DataConfig, DEFAULT_CONFIG

OHLC_COLUMNS = ["open", "high", "low", "close", "volume"]


class DataValidationError(ValueError):
    """Raised when a candle series violates a structural invariant."""


@dataclass(frozen=True)
class Gap:
    """A larger-than-expected gap between two consecutive candles."""
    prev_time: pd.Timestamp
    next_time: pd.Timestamp
    missing_bars: int
    is_session_break: bool

    def __str__(self) -> str:  # pragma: no cover - cosmetic
        kind = "session-break" if self.is_session_break else "INTRA-SESSION"
        return (
            f"{kind} gap: {self.prev_time} -> {self.next_time} "
            f"({self.missing_bars} missing bars)"
        )


def _timeframe_to_timedelta(timeframe: str) -> pd.Timedelta:
    """'5min' -> 5 minutes. Uses pandas' own parser for consistency."""
    return pd.Timedelta(timeframe)


def load_candles(
    path: str,
    timeframe: str,
    cfg: DataConfig = DEFAULT_CONFIG.data,
    validate: bool = True,
) -> pd.DataFrame:
    """Load a candle CSV into a validated, UTC-indexed DataFrame.

    Parameters
    ----------
    path : str
        CSV path. Columns (order-independent): timestamp, open, high, low,
        close, volume. Timestamps are assumed UTC.
    timeframe : str
        Pandas offset alias for the bar size, e.g. '5min' or '15min'. Used for
        gap detection only.
    cfg : DataConfig
    validate : bool
        Run full OHLC/monotonicity validation (default True).

    Returns
    -------
    pd.DataFrame indexed by a tz-aware (UTC) DatetimeIndex named 'timestamp',
    with float columns open/high/low/close/volume, sorted ascending, unique.
    """
    df = pd.read_csv(path)
    return _frame_from_raw(df, timeframe, cfg, validate)


def _frame_from_raw(
    df: pd.DataFrame,
    timeframe: str,
    cfg: DataConfig,
    validate: bool,
) -> pd.DataFrame:
    missing = set(cfg.required_columns) - set(df.columns)
    if missing:
        raise DataValidationError(
            f"CSV missing required columns: {sorted(missing)}. "
            f"Found: {list(df.columns)}"
        )

    df = df.copy()
    # Parse timestamps as UTC. `utc=True` localises naive stamps and converts
    # any offset-aware stamps to UTC — both end up unambiguously UTC.
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)

    for col in OHLC_COLUMNS:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df = df.set_index("timestamp").sort_index()
    df = df[OHLC_COLUMNS]

    if validate:
        validate_ohlc(df)

    return df


def validate_ohlc(df: pd.DataFrame) -> None:
    """Raise DataValidationError on any structural problem. No return value."""
    if df.empty:
        raise DataValidationError("Candle frame is empty.")

    if df.isna().any().any():
        bad = df[df.isna().any(axis=1)]
        raise DataValidationError(
            f"NaN / non-numeric values in {len(bad)} row(s), "
            f"first at {bad.index[0]}."
        )

    if not df.index.is_monotonic_increasing:
        raise DataValidationError("Timestamps are not sorted ascending.")

    dupes = df.index[df.index.duplicated()]
    if len(dupes) > 0:
        raise DataValidationError(
            f"{len(dupes)} duplicate timestamp(s), first at {dupes[0]}."
        )

    # OHLC sanity: high is the max, low is the min of the bar.
    hi, lo, op, cl = df["high"], df["low"], df["open"], df["close"]
    bad_hl = df[hi < lo]
    if len(bad_hl) > 0:
        raise DataValidationError(
            f"{len(bad_hl)} bar(s) with high < low, first at {bad_hl.index[0]}."
        )
    bad_high = df[(hi < op) | (hi < cl)]
    if len(bad_high) > 0:
        raise DataValidationError(
            f"{len(bad_high)} bar(s) where high is below open/close, "
            f"first at {bad_high.index[0]}."
        )
    bad_low = df[(lo > op) | (lo > cl)]
    if len(bad_low) > 0:
        raise DataValidationError(
            f"{len(bad_low)} bar(s) where low is above open/close, "
            f"first at {bad_low.index[0]}."
        )
    if (df[OHLC_COLUMNS[:-1]] <= 0).any().any():
        raise DataValidationError("Non-positive price(s) found in OHLC.")


def detect_gaps(
    df: pd.DataFrame,
    timeframe: str,
    cfg: DataConfig = DEFAULT_CONFIG.data,
) -> list[Gap]:
    """Return gaps between consecutive candles.

    A gap is any step larger than one bar. Gaps longer than
    ``cfg.session_break_hours`` are marked ``is_session_break`` (expected weekend
    / holiday closures). Intra-session gaps larger than ``cfg.max_gap_bars`` are
    the ones worth reviewing; callers filter on ``is_session_break`` /
    ``missing_bars`` as needed.
    """
    if len(df) < 2:
        return []

    step = _timeframe_to_timedelta(timeframe)
    break_delta = pd.Timedelta(hours=cfg.session_break_hours)

    deltas = df.index.to_series().diff().iloc[1:]
    gaps: list[Gap] = []
    for next_time, delta in deltas.items():
        if delta <= step:
            continue
        missing = int(round(delta / step)) - 1
        prev_time = next_time - delta
        gaps.append(
            Gap(
                prev_time=prev_time,
                next_time=next_time,
                missing_bars=missing,
                is_session_break=delta >= break_delta,
            )
        )
    return gaps


def resample(
    df: pd.DataFrame,
    target_timeframe: str,
    cfg: DataConfig = DEFAULT_CONFIG.data,
) -> pd.DataFrame:
    """Resample a base series up to a higher timeframe (e.g. M5 -> M15).

    Bars are left-labelled and left-closed: an M15 bar labelled 07:00 aggregates
    the M5 bars opening at 07:00, 07:05, 07:10 and represents [07:00, 07:15).
    Empty target bins (weekends) are dropped so the output has no synthetic
    all-NaN rows. Aggregation: open=first, high=max, low=min, close=last,
    volume=sum — the only correct OHLC aggregation and, by construction, free of
    look-ahead (each output bar is built solely from bars within its own window).
    """
    if df.empty:
        return df.copy()

    agg = {
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
        "volume": "sum",
    }
    out = (
        df.resample(target_timeframe, label="left", closed="left")
        .agg(agg)
        .dropna(subset=["open"])
    )
    out.index.name = df.index.name
    return out[OHLC_COLUMNS]


def bar_close_time(open_time: pd.Timestamp, timeframe: str) -> pd.Timestamp:
    """The instant a bar becomes CLOSED (= usable). open_time + one bar."""
    return open_time + _timeframe_to_timedelta(timeframe)
