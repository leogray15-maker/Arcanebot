"""Phase 2 — Fractal swing detection.

A swing high (fractal) is a candle whose high is strictly higher than the N
candles on each side; a swing low is the inverse. These are the liquidity
points the strategy hunts.

No-look-ahead is the whole game here. A swing at bar ``i`` needs N candles to
its RIGHT before it can be confirmed, so it is not "known" until bar ``i + N``
has closed. Every Swing therefore carries ``confirmed_index`` — the earliest bar
at which a live system could have known about it. ``swings_known_at`` filters on
that, and the Phase 5 look-ahead test asserts nothing is used before it is
confirmed.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from config import DEFAULT_CONFIG


@dataclass(frozen=True)
class Swing:
    kind: str                 # "high" | "low"
    index: int                # positional index of the swing candle
    time: pd.Timestamp        # open time of the swing candle
    price: float              # the extreme (high for a high, low for a low)
    confirmed_index: int      # earliest bar index at which this swing is known
    confirmed_time: pd.Timestamp

    @property
    def is_high(self) -> bool:
        return self.kind == "high"

    @property
    def is_low(self) -> bool:
        return self.kind == "low"


def find_swings(
    df: pd.DataFrame,
    n: int = DEFAULT_CONFIG.structure.swing_n,
) -> list[Swing]:
    """Return all confirmed fractal swings in ``df`` (chronological order).

    A candle ``i`` (for ``n <= i <= len-1-n``) is a swing high iff its high is
    strictly greater than the highs of the ``n`` candles on each side; a swing
    low iff its low is strictly less than the lows on each side. A candle can be
    both only in degenerate flat data — we treat high and low independently, so
    it may appear as two swings.
    """
    if n < 1:
        raise ValueError("swing_n must be >= 1")

    highs = df["high"].to_numpy()
    lows = df["low"].to_numpy()
    times = df.index
    last = len(df) - 1
    swings: list[Swing] = []

    for i in range(n, len(df) - n):
        left = range(i - n, i)
        right = range(i + 1, i + n + 1)

        if all(highs[i] > highs[j] for j in left) and all(
            highs[i] > highs[j] for j in right
        ):
            ci = i + n
            swings.append(
                Swing("high", i, times[i], float(highs[i]), ci, times[min(ci, last)])
            )

        if all(lows[i] < lows[j] for j in left) and all(
            lows[i] < lows[j] for j in right
        ):
            ci = i + n
            swings.append(
                Swing("low", i, times[i], float(lows[i]), ci, times[min(ci, last)])
            )

    swings.sort(key=lambda s: (s.confirmed_index, s.index))
    return swings


def swings_known_at(swings: list[Swing], index: int) -> list[Swing]:
    """Subset of ``swings`` a live system could know at bar ``index`` (closed)."""
    return [s for s in swings if s.confirmed_index <= index]


def last_swing(
    swings: list[Swing],
    kind: str,
    before_index: int | None = None,
    known_at: int | None = None,
) -> Swing | None:
    """Most recent swing of ``kind`` ("high"/"low").

    ``before_index`` limits to swings whose candle is at or before that index;
    ``known_at`` limits to swings confirmed by that index (no look-ahead). Both
    may be combined.
    """
    candidates = [s for s in swings if s.kind == kind]
    if before_index is not None:
        candidates = [s for s in candidates if s.index <= before_index]
    if known_at is not None:
        candidates = [s for s in candidates if s.confirmed_index <= known_at]
    if not candidates:
        return None
    return max(candidates, key=lambda s: s.index)
