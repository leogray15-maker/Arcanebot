"""Phase 7 — MetaApi (MT5) demo execution adapter.

Implements the SAME ExecutionBackend interface the backtest broker does, so the
identical SignalEngine can drive a demo account with zero signal-code changes.

SAFETY — this class refuses to do anything real until three conditions all hold:
  1. the backtest has been approved by the user (explicit ``approved=True``);
  2. ``TRADING_MODE=demo`` in the environment;
  3. MetaApi credentials are present in a git-ignored ``.env``.
Anything short of that raises ``LiveExecutionBlocked`` before a single network
call. Credentials are read from the environment only — never hard-coded, never
logged. The metaapi SDK is imported lazily so the package isn't a hard
dependency of the backtest.

This is a skeleton: the order-mapping methods are written against the MetaApi
streaming API but are gated off. Do not remove the gate without the user's
explicit go-ahead on demo.
"""

from __future__ import annotations

import os

from config import Config, DEFAULT_CONFIG
from arcanebot.execution.base import (
    ExecutionBackend,
    OrderRequest,
    OrderType,
    Position,
    Side,
)


class LiveExecutionBlocked(RuntimeError):
    """Raised when live/demo execution is attempted without full authorisation."""


def _load_env() -> dict:
    """Load .env if python-dotenv is available; return the relevant vars."""
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except Exception:  # pragma: no cover - dotenv optional at runtime
        pass
    return {
        "mode": os.getenv("TRADING_MODE", ""),
        "token": os.getenv("METAAPI_TOKEN", ""),
        "account_id": os.getenv("METAAPI_ACCOUNT_ID", ""),
        "domain": os.getenv("METAAPI_DOMAIN", "agiliumtrade.agiliumtrade.ai"),
    }


class MetaApiBroker(ExecutionBackend):
    def __init__(self, cfg: Config = DEFAULT_CONFIG, approved: bool = False):
        self.cfg = cfg
        self._approved = approved
        self._env = _load_env()
        self._connection = None
        self._account = None
        self._check_authorised()

    # ------------------------------------------------------------------ #
    # Safety gate
    # ------------------------------------------------------------------ #
    def _check_authorised(self) -> None:
        if not self._approved:
            raise LiveExecutionBlocked(
                "Backtest not approved. Live/demo execution is disabled until the "
                "user reviews backtest results and passes approved=True."
            )
        if self._env["mode"] != "demo":
            raise LiveExecutionBlocked(
                f"TRADING_MODE must be 'demo' for live execution (got "
                f"{self._env['mode']!r}). Refusing to trade."
            )
        if not self._env["token"] or not self._env["account_id"]:
            raise LiveExecutionBlocked(
                "METAAPI_TOKEN / METAAPI_ACCOUNT_ID missing from environment "
                "(.env). Never hard-code credentials."
            )

    # ------------------------------------------------------------------ #
    # Connection lifecycle (async MetaApi SDK, imported lazily)
    # ------------------------------------------------------------------ #
    async def connect(self):  # pragma: no cover - requires network + creds
        self._check_authorised()
        from metaapi_cloud_sdk import MetaApi

        api = MetaApi(self._env["token"], {"domain": self._env["domain"]})
        self._account = await api.metatrader_account_api.get_account(self._env["account_id"])
        # Hard guard: never connect to a non-demo account.
        if getattr(self._account, "type", "") and "demo" not in str(self._account.type).lower():
            raise LiveExecutionBlocked(
                f"Account {self._env['account_id']} is not a demo account. Aborting."
            )
        await self._account.deploy()
        await self._account.wait_connected()
        self._connection = self._account.get_streaming_connection()
        await self._connection.connect()
        await self._connection.wait_synchronized()
        return self._connection

    # ------------------------------------------------------------------ #
    # ExecutionBackend interface
    # ------------------------------------------------------------------ #
    def submit(self, order: OrderRequest) -> None:  # pragma: no cover - gated
        self._check_authorised()
        raise NotImplementedError(
            "Live order submission is intentionally not wired up until you "
            "approve the backtest and enable demo trading. See connect()."
        )

    def cancel_pending(self, reason: str) -> None:  # pragma: no cover - gated
        self._check_authorised()
        raise NotImplementedError("Live cancel not enabled pending approval.")

    @property
    def open_position(self) -> Position | None:  # pragma: no cover - gated
        self._check_authorised()
        return None

    def equity(self) -> float:  # pragma: no cover - gated
        self._check_authorised()
        raise NotImplementedError("Live equity read not enabled pending approval.")
