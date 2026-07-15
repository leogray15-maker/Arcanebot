"""Phase 7 tests — the live/demo execution safety gate.

These assert the adapter refuses to construct (let alone trade) unless the
backtest is approved, TRADING_MODE=demo, and credentials are present. No network
is ever touched.
"""

import pytest

from arcanebot.execution import LiveExecutionBlocked, MetaApiBroker


def test_blocks_without_approval(monkeypatch):
    monkeypatch.setenv("TRADING_MODE", "demo")
    monkeypatch.setenv("METAAPI_TOKEN", "x")
    monkeypatch.setenv("METAAPI_ACCOUNT_ID", "y")
    with pytest.raises(LiveExecutionBlocked, match="not approved"):
        MetaApiBroker(approved=False)


def test_blocks_when_mode_not_demo(monkeypatch):
    monkeypatch.setenv("TRADING_MODE", "backtest")
    monkeypatch.setenv("METAAPI_TOKEN", "x")
    monkeypatch.setenv("METAAPI_ACCOUNT_ID", "y")
    with pytest.raises(LiveExecutionBlocked, match="TRADING_MODE must be 'demo'"):
        MetaApiBroker(approved=True)


def test_blocks_without_credentials(monkeypatch):
    monkeypatch.setenv("TRADING_MODE", "demo")
    monkeypatch.delenv("METAAPI_TOKEN", raising=False)
    monkeypatch.delenv("METAAPI_ACCOUNT_ID", raising=False)
    with pytest.raises(LiveExecutionBlocked, match="missing from environment"):
        MetaApiBroker(approved=True)


def test_constructs_when_fully_authorised(monkeypatch):
    # Even fully authorised, construction must not touch the network — it only
    # passes the gate. Actual trading still requires an explicit connect().
    monkeypatch.setenv("TRADING_MODE", "demo")
    monkeypatch.setenv("METAAPI_TOKEN", "token")
    monkeypatch.setenv("METAAPI_ACCOUNT_ID", "acct")
    broker = MetaApiBroker(approved=True)
    assert broker._connection is None
