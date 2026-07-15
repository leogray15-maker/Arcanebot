"""Phase 2 — Session windows and session-range liquidity.

Two jobs:
  1. Tell the signal engine whether a timestamp is inside an entry kill zone.
  2. Produce session highs/lows (Asian/London/NY) and prior-day highs/lows as
     standing liquidity levels — the raid targets seen in the example trades.

No-look-ahead: a session's high/low is only final once the session has CLOSED,
so every SessionRange carries ``known_from`` = the session end. A prior-day
level is known from the first bar of the following UTC day.

Assumption: each configured session window lies within a single UTC day
(start < end, no midnight wrap). The default Asian/London/NY windows satisfy
this. Cross-midnight sessions would need explicit handling — flagged in README.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, time

import pandas as pd

from config import DEFAULT_CONFIG

SessionCfg = DEFAULT_CONFIG.session


@dataclass(frozen=True)
class SessionRange:
    name: str
    day: date
    start: pd.Timestamp
    end: pd.Timestamp
    high: float
    low: float
    high_time: pd.Timestamp
    low_time: pd.Timestamp
    known_from: pd.Timestamp   # = end; high/low are final only after close


def _window_time_bounds(window: tuple) -> tuple[time, time]:
    sh, sm, eh, em = window
    return time(sh, sm), time(eh, em)


def in_window(ts: pd.Timestamp, window: tuple) -> bool:
    """Is timestamp-of-day within [start, end) of a (sh, sm, eh, em) window?"""
    start, end = _window_time_bounds(window)
    t = ts.time()
    return start <= t < end


def active_entry_session(ts: pd.Timestamp, cfg=SessionCfg) -> str | None:
    """Name of the entry kill zone containing ``ts``, or None."""
    for name in cfg.entry_sessions:
        if in_window(ts, getattr(cfg, name)):
            return name
    return None


def compute_session_ranges(
    df: pd.DataFrame,
    cfg=SessionCfg,
    sessions: tuple | None = None,
) -> list[SessionRange]:
    """Per-UTC-day high/low for each named session window.

    ``sessions`` defaults to ``cfg.liquidity_sessions``. Only days that actually
    contain candles inside the window produce a range.
    """
    if df.empty:
        return []
    names = sessions if sessions is not None else cfg.liquidity_sessions
    out: list[SessionRange] = []

    days = pd.Index(df.index.normalize().unique())
    for name in names:
        window = getattr(cfg, name)
        start_t, end_t = _window_time_bounds(window)
        for day_start in days:
            day = day_start.date()
            start_ts = day_start + pd.Timedelta(hours=start_t.hour, minutes=start_t.minute)
            end_ts = day_start + pd.Timedelta(hours=end_t.hour, minutes=end_t.minute)
            # Bars whose OPEN falls in [start, end).
            mask = (df.index >= start_ts) & (df.index < end_ts)
            chunk = df.loc[mask]
            if chunk.empty:
                continue
            hi_time = chunk["high"].idxmax()
            lo_time = chunk["low"].idxmin()
            out.append(
                SessionRange(
                    name=name,
                    day=day,
                    start=start_ts,
                    end=end_ts,
                    high=float(chunk["high"].max()),
                    low=float(chunk["low"].min()),
                    high_time=hi_time,
                    low_time=lo_time,
                    known_from=end_ts,
                )
            )
    out.sort(key=lambda s: s.known_from)
    return out


@dataclass(frozen=True)
class DayRange:
    day: date
    high: float
    low: float
    known_from: pd.Timestamp   # first instant of the NEXT day


def compute_prior_day_levels(df: pd.DataFrame) -> list[DayRange]:
    """Full-day high/low per UTC day, usable as liquidity from the next day on."""
    if df.empty:
        return []
    grouped = df.groupby(df.index.normalize())
    out: list[DayRange] = []
    for day_start, chunk in grouped:
        out.append(
            DayRange(
                day=day_start.date(),
                high=float(chunk["high"].max()),
                low=float(chunk["low"].min()),
                known_from=day_start + pd.Timedelta(days=1),
            )
        )
    out.sort(key=lambda d: d.day)
    return out
