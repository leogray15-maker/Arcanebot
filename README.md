# Arcanebot — XAUUSD ICT session-liquidity bot

Backtest-first automated trading bot for **XAUUSD only**, broker **IC Markets**,
built on an ICT session-liquidity-raid + market-structure-shift model.

> **Status:** All build phases (1–7) implemented and tested — **43 tests
> passing**, including the look-ahead guard. The backtest runs end to end on M5
> data and produces a trade log, summary stats, and plots. The MetaApi demo
> adapter exists but is **hard-gated off**: no live/demo execution until you
> review the backtest and explicitly approve.

## Hard rules (enforced across the codebase)

1. **No look-ahead.** At each simulated timestamp only closed candles up to and
   including that bar are visible. A dedicated test (Phase 5) fails if any
   indicator/signal reads a future candle.
2. **Signals fire on candle close only.**
3. **Demo only** for live execution, after approval. Credentials load from a
   git-ignored `.env` (see `.env.example`); never hard-coded.
4. **Risk cap:** fixed % risk/trade (default 0.5%), max 1 open position, hard
   daily loss limit (default 2%).
5. **Every trade decision is logged** with the triggering rule, entry/SL/TP and
   the price levels involved.

## Repo layout

```
config.py                 # ALL tunable numbers live here (sweep without touching logic)
arcanebot/
  data/                   # Phase 1 — load/validate CSV, resample M5->M15, gap detection  ✅
  structure/              # Phase 2 — swings, sessions, liquidity, FVG/OB/VI               ✅
  signals/                # Phase 3 — sweep -> MSS -> PD-array entry state machine          ✅
  backtest/               # Phase 4 — event-driven loop, broker, stats, CLI                 ✅
  reporting/              # Phase 6 — equity curve, per-session/R breakdowns                ✅
  execution/base.py       # shared broker interface (backtest + live share signal code)    ✅
  execution/metaapi_adapter.py  # Phase 7 — demo adapter, hard-gated off                    ✅
scripts/generate_sample_data.py # synthetic M5 for an end-to-end dry run
tests/                    # unit tests + fixtures
data/                     # your CSVs (git-ignored)
outputs/                  # backtest artefacts (git-ignored)
```

## Build sequence

1. **Data layer** — load/validate CSVs, resample M5→M15, gap handling. ✅
2. **Structure engine** — swing detection, session ranges, liquidity, FVG/OB/VI. ✅
3. **Signal engine** — sweep → MSS → PD-array entry. ✅
4. **Backtest loop** — event-driven fills, spread/slippage, equity, daily loss cap. ✅
5. **Look-ahead test** — truncation-invariance across the whole pipeline. ✅
6. **Reporting** — equity curve, per-session, R distribution. ✅
7. **Live/demo execution (MetaApi)** — implemented but gated off until approval. ✅

## Setup & running

```bash
pip install -r requirements.txt
python -m pytest -q                                   # run the 43-test suite

# End-to-end dry run on SYNTHETIC data (not market data — pipeline check only):
python scripts/generate_sample_data.py --days 20 --out data/sample_xauusd_m5.csv
python -m arcanebot.backtest.run --m5 data/sample_xauusd_m5.csv --report

# Real run once you drop in IC Markets CSVs:
python -m arcanebot.backtest.run --m5 data/xauusd_m5.csv --m15 data/xauusd_m15.csv --report
```

CSV format (per spec): columns `timestamp, open, high, low, close, volume`,
timestamps UTC. If `--m15` is omitted it is resampled from the M5 file. Outputs
land in `outputs/`: `trades.csv`, `decisions.log`, `summary.txt`, and PNG plots.

## Honest caveats (read before trusting any number)

- The included `data/sample_*.csv` is a **random walk**, not gold. Its results
  are meaningless for edge — it exists only to prove the pipeline runs. Expect
  ~0 or negative expectancy on it (a good sign: no fake edge). Judge the strategy
  only on **real IC Markets data**.
- The ICT rules were **codified into concrete, testable definitions** (MSS swing,
  discount filter, PD-array tie-breaks). Reasonable people draw these lines
  differently — every choice is a knob in `config.py`. Tune, don't rewrite.
- Fills use OHLC only: limit fills when price trades through the level; when a
  bar spans both SL and TP we assume **SL first** (pessimistic). Spread (20¢) and
  slippage (1 pip) push every fill to the worse side.
- **Skepticism rule:** the CLI flags any run with >2R expectancy — that usually
  means a bug (look-ahead, unrealistic fills, or curve-fit), not a gold mine.

---

## My understanding of the long setup (restated)

During a **kill zone** (London 07:00–10:00 or NY-AM 12:00–15:00 UTC), the bot
watches M5 price for this sequence:

1. **Liquidity target.** Track recent sellside liquidity — a prior swing low
   (fractal, N=2 by default), or a cluster of roughly-equal lows.
2. **Sweep / raid.** A candle's low pokes **below** that swing low but price
   **closes back above** it (same or next candle). That's the trigger to start
   watching for a bullish reversal.
3. **Displacement.** An impulsive up-move — at least one candle whose body ≥ 1.5×
   the average body of the last 10 candles — leaving an inefficiency (FVG)
   behind.
4. **Market Structure Shift (MSS).** Price **body-closes above** the most recent
   short-term swing high that formed on the way down into the sweep. This
   confirms the bullish shift.
5. **Entry (PD array).** Place a **buy limit** into the nearest discount PD array
   left by the displacement — an FVG, a bullish order block (last down-candle
   before the up-move), or a volume imbalance — preferring the deepest one still
   in the discount half of the sweep→MSS range.
6. **Invalidation.** Cancel the pending setup if price closes **below the sweep
   low** before filling, or when the kill-zone window ends.
7. **Stop loss.** A few cents (default 15¢) below the sweep low.
8. **Take profit.** The opposing buyside liquidity — nearest prior swing high
   above — with an optional fixed-R mode (default 2R) for comparison, and an
   optional move-to-breakeven at 1R.

The **short setup is the exact mirror**: sweep of a swing high → close back
below → MSS below a short-term low → sell limit at a bearish PD array → SL above
the sweep high → TP at sellside liquidity.

---

## Decisions locked in (from the spec + your example trades)

- **Execution: M5 entry, M15 structure.** Liquidity levels and fractal structure
  come from M15; the sweep → MSS → displacement → limit entry all evaluate on
  closed **M5** candles.
- **Liquidity = session ranges + fractal swings.** Track Asian/London/NY session
  highs-lows and prior-day levels *and* M15 fractal swings (N=2); a sweep of
  either can arm a setup.
- **Take profit = opposing liquidity, with a fixed-2R shadow logged on every
  trade** so the two exit styles can be compared on identical entries.
- **Entries only in London (07:00–10:00) and NY-AM (12:00–15:00) UTC.** Asian is
  liquidity-only (no entries).

## Remaining smaller defaults (coded; override anytime)

My proposed default is in **bold**; none of these block Phase 2.

1. **MSS internal swing.** The "short-term swing high in the down-move into the
   sweep" = the **most recent M5 fractal high (N=2)** between the start of the
   leg down and the sweep; MSS = first M5 body-close above it.
2. **Sweep re-entry window.** Close back over the level within the sweep candle
   **itself or the next 1 candle** (`sweep_reentry_bars=1`).
3. **PD-array pick.** Filter to arrays in the **discount half** of sweep→MSS,
   take the **deepest** (closest to sweep low), tie-break FVG > OB > VI.
4. **Equal-level tolerance.** **15¢** (`equal_level_tolerance`).
5. **New setups per window.** A new setup may form after one invalidates, as long
   as no position is open (max 1 position).

## Still need your confirmation

- **Gold pip/point convention (affects slippage & sizing).** Proposed: **1 pip =
  10¢ (0.10), 1 point = 1¢**, so 20¢ spread = 2 pips, 1-pip slippage = 10¢.
- **Asian session window.** Defaulted to **00:00–07:00 UTC** as a guess — what
  exact hours does your "Asian" box use?
- **Session DST.** Kill zones are **fixed UTC** (no seasonal shift) per spec —
  confirming that's intended.

These don't block Phase 2 (structure engine); I'll proceed with the defaults and
you can correct the three above whenever.
