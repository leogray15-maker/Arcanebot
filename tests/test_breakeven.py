"""Phase 4 tests — breakeven-at-R stop management (a real profitability lever)."""

from dataclasses import replace

import pandas as pd

from config import DEFAULT_CONFIG
from arcanebot.backtest.broker import BacktestBroker
from arcanebot.execution.base import OrderRequest, OrderType, Side


def _bar(o, h, l, c):
    return pd.Series({"open": o, "high": h, "low": l, "close": c, "volume": 100})


def _cfg(be):
    # Zero costs for clean arithmetic; set the breakeven trigger.
    cfg = DEFAULT_CONFIG
    cfg = replace(cfg, costs=replace(cfg.costs, spread=0.0, slippage=0.0))
    cfg = replace(cfg, risk=replace(cfg.risk, breakeven_at_r=be))
    return cfg


def _order(t0):
    return OrderRequest(
        side=Side.LONG, order_type=OrderType.LIMIT, entry_price=100.0,
        stop_loss=98.0, take_profit=110.0, lots=0.0, created_at=t0,
        expires_at=None, reason="test session=london",
    )


def test_breakeven_moves_stop_to_entry():
    t = [pd.Timestamp("2024-01-02 07:00", tz="UTC") + pd.Timedelta(minutes=5 * k) for k in range(4)]
    broker = BacktestBroker(_cfg(be=1.0))   # move to BE after +1R (=102)
    broker.submit(_order(t[0]))

    broker.process_bar(_bar(100, 101, 100, 100.5), 1, t[1])   # fills at 100
    assert broker.open_position is not None
    broker.process_bar(_bar(100.5, 103, 100.4, 102.5), 2, t[2])  # prints +1R -> arm BE
    broker.process_bar(_bar(102, 102.5, 99.5, 100.0), 3, t[3])   # dips to entry -> BE stop

    assert len(broker.trades) == 1
    tr = broker.trades[0]
    assert tr.exit_reason == "SL"
    assert abs(tr.r_multiple) < 0.05          # ~breakeven, NOT -1R


def test_without_breakeven_same_path_stays_open():
    t = [pd.Timestamp("2024-01-02 07:00", tz="UTC") + pd.Timedelta(minutes=5 * k) for k in range(4)]
    broker = BacktestBroker(_cfg(be=None))    # breakeven disabled
    broker.submit(_order(t[0]))

    broker.process_bar(_bar(100, 101, 100, 100.5), 1, t[1])
    broker.process_bar(_bar(100.5, 103, 100.4, 102.5), 2, t[2])
    broker.process_bar(_bar(102, 102.5, 99.5, 100.0), 3, t[3])   # dip to 99.5 > SL 98

    # No breakeven -> the dip to entry doesn't stop us out; still open.
    assert broker.open_position is not None
    assert broker.trades == []
