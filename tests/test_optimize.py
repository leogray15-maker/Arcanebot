"""Tests — parameter sweep and walk-forward machinery (mechanics, not edge)."""

from dataclasses import replace

from config import DEFAULT_CONFIG
from arcanebot.backtest.optimize import apply_overrides, sweep
from arcanebot.data import load_candles, resample


def test_apply_overrides_sets_nested_fields():
    cfg = apply_overrides(DEFAULT_CONFIG, {
        "risk.fixed_r_target": 3.0,
        "structure.swing_n": 4,
        "risk.breakeven_at_r": 1.0,
    })
    assert cfg.risk.fixed_r_target == 3.0
    assert cfg.structure.swing_n == 4
    assert cfg.risk.breakeven_at_r == 1.0
    # Untouched fields preserved.
    assert cfg.risk.risk_per_trade_pct == DEFAULT_CONFIG.risk.risk_per_trade_pct


def test_sweep_returns_one_row_per_combo():
    m5 = load_candles("data/sample_xauusd_m5.csv", "5min").iloc[:1500]
    m15 = resample(m5, "15min")
    grid = {
        "risk.fixed_r_target": [2.0, 3.0],
        "risk.tp_mode": ["fixed_r"],
    }
    df = sweep(m5, m15, grid, DEFAULT_CONFIG)
    assert len(df) == 2
    assert {"expectancy_r", "profit_factor", "trades"}.issubset(df.columns)
    # Ranked best-first by expectancy.
    assert df["expectancy_r"].is_monotonic_decreasing
