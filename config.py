"""Central configuration for the Arcanebot XAUUSD ICT strategy.

EVERY tunable number lives here so parameters can be swept without touching
logic. Values are grouped by the phase / engine that consumes them. Nothing in
here executes strategy logic — it is pure data.

Units note for XAUUSD (gold):
  - Price is quoted in USD per ounce, e.g. 2354.30.
  - We define 1 "point" = 0.01 USD (a cent) and 1 "pip" = 0.10 USD (ten cents).
    So the default 20-cent spread = 0.20 USD, and 1 pip of slippage = 0.10 USD.
  - This pip/point convention is a proposed default (see README "Open questions").
"""

from dataclasses import dataclass, field


# --------------------------------------------------------------------------- #
# Instrument / units
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class InstrumentConfig:
    symbol: str = "XAUUSD"
    # USD value of a 1.0 price move for 1.0 lot. For IC Markets gold, 1 lot =
    # 100 oz, so a $1 move = $100 per lot. Used only for position sizing / PnL.
    usd_per_price_per_lot: float = 100.0
    # Unit conventions (see module docstring).
    point: float = 0.01   # one cent
    pip: float = 0.10     # ten cents
    min_lot: float = 0.01
    lot_step: float = 0.01


# --------------------------------------------------------------------------- #
# Data layer (Phase 1)
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class DataConfig:
    # Expected CSV columns (order-independent; validated on load).
    required_columns: tuple = ("timestamp", "open", "high", "low", "close", "volume")
    # Timezone of incoming timestamps. Spec: UTC.
    timezone: str = "UTC"
    # Base (entry) timeframe and the higher timeframe derived from it.
    # DECIDED: entry logic (sweep -> MSS -> displacement -> limit) fires on
    # closed M5 candles; liquidity levels + fractal structure come from M15.
    base_timeframe: str = "5min"        # M5 — signals fire here (entry_timeframe)
    htf_timeframe: str = "15min"        # M15 — HTF structure/liquidity
    entry_timeframe: str = "5min"       # explicit alias used by the signal engine
    structure_timeframe: str = "15min"  # explicit alias for liquidity/fractals
    # A gap larger than this many base bars (during an otherwise-continuous
    # trading stretch) is flagged for review. Weekends are expected and ignored.
    max_gap_bars: int = 3
    # Weekend/holiday gaps that exceed this many hours are treated as an
    # expected market closure, not a data error.
    session_break_hours: float = 6.0


# --------------------------------------------------------------------------- #
# Structure engine (Phase 2)
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class StructureConfig:
    # Fractal swing: high strictly higher than N candles each side (inverse low).
    swing_n: int = 2
    # Two highs/lows within this many USD are treated as "equal" liquidity.
    equal_level_tolerance: float = 0.15   # 15 cents
    # Order block entry zone: "body" (open->close) or "range" (full high->low).
    ob_zone_mode: str = "body"
    # DECIDED (from example trades): raided liquidity is BOTH session
    # highs/lows (Asian/London/prior session) AND M15 fractal swings.
    #   "session_and_fractal" | "fractal" | "session"
    liquidity_source: str = "session_and_fractal"
    track_session_levels: bool = True
    track_fractal_levels: bool = True
    # Also carry prior-day high/low as standing liquidity.
    track_prior_day_levels: bool = True


# --------------------------------------------------------------------------- #
# Signal engine (Phase 3)
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class SignalConfig:
    # Displacement: a candle body >= this multiple of the average body of the
    # last `displacement_lookback` candles.
    displacement_body_mult: float = 1.5
    displacement_lookback: int = 10
    # A sweep must close back over the swept level within this many candles
    # (inclusive of the sweep candle itself).
    sweep_reentry_bars: int = 1
    # PD-array selection priority order (first found wins among equals).
    pd_array_priority: tuple = ("FVG", "OB", "VI")
    # Prefer the deepest PD array within the discount half of sweep->MSS range.
    prefer_discount_half: bool = True


# --------------------------------------------------------------------------- #
# Sessions / kill zones (UTC) — Phase 3/4
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class SessionConfig:
    # (start_hour, start_min, end_hour, end_min) in UTC. Fixed UTC windows
    # (no DST shifting) per spec — configurable here.
    # ENTRY kill zones:
    london: tuple = (7, 0, 10, 0)
    new_york_am: tuple = (12, 0, 15, 0)
    # LIQUIDITY-ONLY session (no entries): Asian range whose high/low London
    # tends to raid. Default window is a proposed guess — CONFIRM exact hours.
    asian: tuple = (0, 0, 7, 0)
    # Which sessions allow new entries vs. only contribute liquidity levels.
    entry_sessions: tuple = ("london", "new_york_am")
    liquidity_sessions: tuple = ("asian", "london", "new_york_am")
    # If True, an unfilled pending setup is cancelled when its kill zone ends.
    cancel_on_killzone_end: bool = True


# --------------------------------------------------------------------------- #
# Risk / execution model (Phase 4)
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class RiskConfig:
    starting_equity: float = 10_000.0
    risk_per_trade_pct: float = 0.5      # % of current equity risked per trade
    max_open_positions: int = 1
    daily_loss_limit_pct: float = 2.0    # stop trading for the day if hit
    # SL buffer beyond the sweep extreme.
    sl_buffer: float = 0.15              # 15 cents
    # DECIDED: primary TP = opposing liquidity; on EVERY trade also record what
    # a fixed-R exit would have returned, for side-by-side expectancy.
    tp_mode: str = "liquidity"           # "liquidity" | "fixed_r"
    fixed_r_target: float = 2.0
    always_log_fixed_r_shadow: bool = True
    # Move SL to breakeven once price reaches this many R (None disables).
    breakeven_at_r: float | None = None


@dataclass(frozen=True)
class ExecutionCostConfig:
    # Spread applied to entries/exits (total, in USD). Split half to each side
    # is handled in the fill model; this is the full quoted spread.
    spread: float = 0.20                 # 20 cents
    slippage: float = 0.10               # 1 pip = 10 cents
    # When both SL and TP fall inside the same candle's range, assume the
    # worse outcome (SL hit first). Conservative anti-look-ahead default.
    pessimistic_intrabar_fills: bool = True


# --------------------------------------------------------------------------- #
# Top-level aggregate
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Config:
    instrument: InstrumentConfig = field(default_factory=InstrumentConfig)
    data: DataConfig = field(default_factory=DataConfig)
    structure: StructureConfig = field(default_factory=StructureConfig)
    signal: SignalConfig = field(default_factory=SignalConfig)
    session: SessionConfig = field(default_factory=SessionConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    costs: ExecutionCostConfig = field(default_factory=ExecutionCostConfig)


# Default singleton used across the codebase. Import and override fields
# (via dataclasses.replace) for parameter sweeps.
DEFAULT_CONFIG = Config()
