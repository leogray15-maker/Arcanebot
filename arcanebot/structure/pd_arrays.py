"""Phase 2 — PD arrays: Fair Value Gaps, Order Blocks, Volume Imbalances.

These are the entry zones the displacement leaves behind. Each PDArray carries a
``confirmed_index`` (the bar after which the pattern is fully formed and known),
so the signal engine and the look-ahead test can enforce no forward reads.

Definitions (bullish; bearish is the mirror):
  FVG  — 3 candles c1,c2,c3 with c1.high < c3.low. Gap zone = [c1.high, c3.low].
  OB   — the last down-candle (close < open) before an up displacement. Zone is
         its body (open->close) or full range, per ob_zone_mode.
  VI   — two adjacent candles whose bodies gap (c2.open > c1.close) while their
         wicks still overlap (c1.high >= c2.low). Zone = [c1.close, c2.open].
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from config import DEFAULT_CONFIG

StructCfg = DEFAULT_CONFIG.structure


@dataclass(frozen=True)
class PDArray:
    kind: str            # "FVG" | "OB" | "VI"
    side: str            # "bullish" | "bearish"
    top: float
    bottom: float
    index: int           # anchor bar (c3 for FVG, OB candle, c2 for VI)
    time: pd.Timestamp
    confirmed_index: int
    confirmed_time: pd.Timestamp

    @property
    def midpoint(self) -> float:
        return (self.top + self.bottom) / 2.0

    @property
    def height(self) -> float:
        return self.top - self.bottom

    def contains(self, price: float) -> bool:
        return self.bottom <= price <= self.top


# --------------------------------------------------------------------------- #
# Fair Value Gaps
# --------------------------------------------------------------------------- #
def find_fvgs(df: pd.DataFrame, side: str | None = None) -> list[PDArray]:
    """All FVGs in ``df``. ``side`` filters to 'bullish'/'bearish' if given."""
    highs = df["high"].to_numpy()
    lows = df["low"].to_numpy()
    times = df.index
    out: list[PDArray] = []

    for i in range(len(df) - 2):
        c1_high, c1_low = highs[i], lows[i]
        c3_high, c3_low = highs[i + 2], lows[i + 2]
        c3_idx = i + 2

        # Bullish: gap between c1 high and c3 low (price ran up leaving a void).
        if c1_high < c3_low:
            out.append(
                PDArray("FVG", "bullish", float(c3_low), float(c1_high),
                        c3_idx, times[c3_idx], c3_idx, times[c3_idx])
            )
        # Bearish: gap between c3 high and c1 low.
        if c1_low > c3_high:
            out.append(
                PDArray("FVG", "bearish", float(c1_low), float(c3_high),
                        c3_idx, times[c3_idx], c3_idx, times[c3_idx])
            )

    if side is not None:
        out = [p for p in out if p.side == side]
    return out


# --------------------------------------------------------------------------- #
# Order Blocks
# --------------------------------------------------------------------------- #
def find_order_block(
    df: pd.DataFrame,
    displacement_index: int,
    side: str,
    mode: str = StructCfg.ob_zone_mode,
) -> PDArray | None:
    """The order block preceding the displacement candle at ``displacement_index``.

    Bullish setup (``side='bullish'``): scan left from the displacement candle
    for the nearest DOWN candle (close < open) — that is the +OB. Bearish: the
    nearest UP candle (close > open). Returns None if none found before the frame
    start. ``mode`` = 'body' (open->close) or 'range' (low->high).
    """
    if side not in ("bullish", "bearish"):
        raise ValueError("side must be 'bullish' or 'bearish'")
    opens = df["open"].to_numpy()
    closes = df["close"].to_numpy()
    highs = df["high"].to_numpy()
    lows = df["low"].to_numpy()
    times = df.index

    def is_down(k: int) -> bool:
        return closes[k] < opens[k]

    def is_up(k: int) -> bool:
        return closes[k] > opens[k]

    want = is_down if side == "bullish" else is_up

    for k in range(displacement_index - 1, -1, -1):
        if want(k):
            if mode == "body":
                top = float(max(opens[k], closes[k]))
                bottom = float(min(opens[k], closes[k]))
            elif mode == "range":
                top = float(highs[k])
                bottom = float(lows[k])
            else:
                raise ValueError("ob_zone_mode must be 'body' or 'range'")
            # Known only once the displacement candle has closed.
            return PDArray("OB", side, top, bottom, k, times[k],
                           displacement_index, times[displacement_index])
    return None


# --------------------------------------------------------------------------- #
# Volume Imbalances
# --------------------------------------------------------------------------- #
def find_volume_imbalances(df: pd.DataFrame, side: str | None = None) -> list[PDArray]:
    """Adjacent-candle body gaps whose wicks still overlap (thin entry zones)."""
    opens = df["open"].to_numpy()
    closes = df["close"].to_numpy()
    highs = df["high"].to_numpy()
    lows = df["low"].to_numpy()
    times = df.index
    out: list[PDArray] = []

    for i in range(len(df) - 1):
        c2 = i + 1
        # Bullish VI: body of c2 opens above body-top of c1, but wicks overlap
        # (so it is not a full FVG). Zone between c1.close and c2.open.
        if opens[c2] > closes[i] and highs[i] >= lows[c2]:
            out.append(
                PDArray("VI", "bullish", float(opens[c2]), float(closes[i]),
                        c2, times[c2], c2, times[c2])
            )
        # Bearish VI: c2 opens below c1.close with overlapping wicks.
        if opens[c2] < closes[i] and lows[i] <= highs[c2]:
            out.append(
                PDArray("VI", "bearish", float(closes[i]), float(opens[c2]),
                        c2, times[c2], c2, times[c2])
            )

    if side is not None:
        out = [p for p in out if p.side == side]
    return out
