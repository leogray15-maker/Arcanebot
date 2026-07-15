"""Phase 7 — Live/demo runner skeleton.

Demonstrates that the SAME SignalEngine used in the backtest drives live/demo
execution: it consumes closed M5 bars and calls ``engine.evaluate`` exactly as
the backtest loop does, sending any OrderRequest to a MetaApiBroker instead of
the BacktestBroker. It is gated — nothing runs until the user approves the
backtest and enables demo mode (the MetaApiBroker constructor enforces this).

This file is intentionally a skeleton; wiring the streaming callbacks is the
first task of the post-approval live phase.
"""

from __future__ import annotations

import pandas as pd

from config import Config, DEFAULT_CONFIG
from arcanebot.execution.metaapi_adapter import MetaApiBroker
from arcanebot.signals.engine import BrokerView, SignalEngine


async def run_live(  # pragma: no cover - requires approval, creds, network
    warmup_m5: pd.DataFrame,
    warmup_m15: pd.DataFrame,
    cfg: Config = DEFAULT_CONFIG,
    approved: bool = False,
) -> None:
    """Skeleton live loop. Raises via the broker gate unless fully authorised."""
    broker = MetaApiBroker(cfg, approved=approved)   # enforces the safety gate
    await broker.connect()

    engine = SignalEngine(cfg)
    engine.prepare(warmup_m5, warmup_m15)

    # On each NEW closed M5 bar delivered by the MetaApi stream:
    #   1. append it to the working frame and re-run engine.prepare (or an
    #      incremental update) so structure/liquidity reflect the new bar;
    #   2. view = BrokerView(broker.open_position is not None, has_pending, stop);
    #   3. decision = engine.evaluate(last_index, view);
    #   4. broker.cancel_pending(...) / broker.submit(decision.submit) as needed.
    #
    # The engine code is byte-for-byte the same as the backtest — only the
    # ExecutionBackend implementation differs.
    raise NotImplementedError(
        "Live streaming loop is not enabled until backtest approval."
    )
