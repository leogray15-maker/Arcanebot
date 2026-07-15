"""Phase 2 — Liquidity pool.

Unifies the three liquidity sources the example trades use into one buyside
(highs) / sellside (lows) pool:
  * M15 fractal swings          -> source "fractal"
  * session highs/lows          -> source "session:<name>"
  * prior-day highs/lows        -> source "prior_day"

Every level carries ``known_from`` (a UTC timestamp) so callers can ask only for
levels a live system would already know — swings use their confirmation time,
session levels their session close, prior-day levels the next day's open.

Equal highs/lows within ``equal_level_tolerance`` can be clustered into a single
"relatively equal" level (the resting-liquidity pool ICT targets).
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from config import DEFAULT_CONFIG
from arcanebot.structure.sessions import DayRange, SessionRange
from arcanebot.structure.swings import Swing

StructCfg = DEFAULT_CONFIG.structure


@dataclass(frozen=True)
class LiquidityLevel:
    side: str            # "buyside" (a high) | "sellside" (a low)
    price: float
    source: str          # "fractal" | "session:<name>" | "prior_day"
    ref_time: pd.Timestamp
    known_from: pd.Timestamp
    count: int = 1       # how many raw levels this represents (equal-highs)


def build_liquidity(
    swings: list[Swing] | None = None,
    session_ranges: list[SessionRange] | None = None,
    day_ranges: list[DayRange] | None = None,
    cfg=StructCfg,
) -> list[LiquidityLevel]:
    """Assemble a liquidity pool honoring the config toggles / liquidity_source."""
    use_fractal = cfg.track_fractal_levels and cfg.liquidity_source in (
        "fractal", "session_and_fractal",
    )
    use_session = cfg.track_session_levels and cfg.liquidity_source in (
        "session", "session_and_fractal",
    )
    levels: list[LiquidityLevel] = []

    if use_fractal and swings:
        for s in swings:
            side = "buyside" if s.is_high else "sellside"
            levels.append(
                LiquidityLevel(side, s.price, "fractal", s.time, s.confirmed_time)
            )

    if use_session and session_ranges:
        for sr in session_ranges:
            levels.append(
                LiquidityLevel("buyside", sr.high, f"session:{sr.name}",
                               sr.high_time, sr.known_from)
            )
            levels.append(
                LiquidityLevel("sellside", sr.low, f"session:{sr.name}",
                               sr.low_time, sr.known_from)
            )

    if cfg.track_prior_day_levels and day_ranges:
        for dr in day_ranges:
            ts = pd.Timestamp(dr.day, tz="UTC")
            levels.append(
                LiquidityLevel("buyside", dr.high, "prior_day", ts, dr.known_from)
            )
            levels.append(
                LiquidityLevel("sellside", dr.low, "prior_day", ts, dr.known_from)
            )

    levels.sort(key=lambda lv: (lv.known_from, lv.price))
    return levels


def levels_known_at(
    levels: list[LiquidityLevel], ts: pd.Timestamp
) -> list[LiquidityLevel]:
    """Only levels a live system would already know at time ``ts``."""
    return [lv for lv in levels if lv.known_from <= ts]


def cluster_equal_levels(
    levels: list[LiquidityLevel],
    tolerance: float = StructCfg.equal_level_tolerance,
) -> list[LiquidityLevel]:
    """Merge same-side levels within ``tolerance`` into single 'equal' levels.

    The merged level takes the extreme price (highest for buyside, lowest for
    sellside — the actual liquidity resting point), the earliest ``known_from``
    among members, and a ``count`` of how many were merged. Sources are joined.
    """
    merged: list[LiquidityLevel] = []
    for side in ("buyside", "sellside"):
        side_levels = sorted(
            (lv for lv in levels if lv.side == side), key=lambda lv: lv.price
        )
        i = 0
        while i < len(side_levels):
            group = [side_levels[i]]
            j = i + 1
            while j < len(side_levels) and (
                side_levels[j].price - group[0].price
            ) <= tolerance:
                group.append(side_levels[j])
                j += 1
            if len(group) == 1:
                merged.append(group[0])
            else:
                price = max(g.price for g in group) if side == "buyside" else min(
                    g.price for g in group
                )
                known_from = min(g.known_from for g in group)
                ref_time = min(g.ref_time for g in group)
                sources = "+".join(sorted({g.source for g in group}))
                merged.append(
                    LiquidityLevel(side, price, sources, ref_time, known_from,
                                   count=sum(g.count for g in group))
                )
            i = j
    merged.sort(key=lambda lv: (lv.known_from, lv.price))
    return merged


def nearest_sellside_below(
    levels: list[LiquidityLevel], price: float
) -> LiquidityLevel | None:
    """Nearest sellside (low) level strictly below ``price`` — a raid target."""
    below = [lv for lv in levels if lv.side == "sellside" and lv.price < price]
    return max(below, key=lambda lv: lv.price) if below else None


def nearest_buyside_above(
    levels: list[LiquidityLevel], price: float
) -> LiquidityLevel | None:
    """Nearest buyside (high) level strictly above ``price`` — a long's TP."""
    above = [lv for lv in levels if lv.side == "buyside" and lv.price > price]
    return min(above, key=lambda lv: lv.price) if above else None
