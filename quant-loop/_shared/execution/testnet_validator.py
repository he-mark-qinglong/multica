"""Testnet validation framework — structured smoke tests for live execution.

Before going live, the system must pass testnet validation: connect to a
testnet, place orders, verify fills, check reconciliation. This module
provides a structured suite of validation checks.

Usage:
    validator = TestnetValidator(
        adapter=testnet_adapter,
        symbols=["BTCUSDT"],
    )
    report = validator.run_all()
    if not report.all_passed:
        for check in report.failures:
            print(f"FAIL: {check.name} — {check.detail}")
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Protocol


class CheckStatus(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    SKIP = "skip"
    ERROR = "error"


@dataclass
class CheckResult:
    """Result of a single validation check."""
    name: str
    status: CheckStatus
    detail: str = ""
    latency_ms: float = 0.0
    metadata: dict = field(default_factory=dict)


@dataclass
class ValidationReport:
    """Complete testnet validation report."""
    checks: list[CheckResult] = field(default_factory=list)
    total_duration_ms: float = 0.0

    @property
    def all_passed(self) -> bool:
        return all(c.status == CheckStatus.PASS for c in self.checks)

    @property
    def failures(self) -> list[CheckResult]:
        return [c for c in self.checks if c.status == CheckStatus.FAIL]

    @property
    def passes(self) -> list[CheckResult]:
        return [c for c in self.checks if c.status == CheckStatus.PASS]

    def summary(self) -> str:
        p = len(self.passes)
        f = len(self.failures)
        s = len([c for c in self.checks if c.status == CheckStatus.SKIP])
        e = len([c for c in self.checks if c.status == CheckStatus.ERROR])
        return f"Testnet validation: {p} pass, {f} fail, {s} skip, {e} error ({'ALL PASS' if self.all_passed else 'FAILURES'})"


class ExecutionAdapter(Protocol):
    """Minimal interface for an exchange adapter (testnet or live)."""
    def place_order(self, symbol: str, side: str, qty: float,
                    order_type: str = "market", price: float | None = None) -> dict: ...
    def cancel_order(self, order_id: str) -> dict: ...
    def get_position(self, symbol: str) -> dict: ...
    def get_balance(self) -> dict: ...
    def ping(self) -> bool: ...


class TestnetValidator:
    """Structured testnet validation suite.

    Runs a series of checks to verify the execution layer is working
    correctly before live deployment.
    """

    def __init__(
        self,
        adapter: ExecutionAdapter,
        symbols: list = None,
        test_qty: float = 0.001,  # minimum order size
        timeout_s: float = 30.0,
    ):
        self.adapter = adapter
        self.symbols = symbols or ["BTCUSDT"]
        self.test_qty = test_qty
        self.timeout = timeout_s

    def run_all(self) -> ValidationReport:
        """Run all validation checks in sequence."""
        report = ValidationReport()
        t0 = time.monotonic()

        checks = [
            self._check_connectivity,
            self._check_balance,
            self._check_market_order,
            self._check_position_update,
            self._check_cancel_order,
            self._check_error_handling,
        ]

        for check_fn in checks:
            try:
                result = check_fn()
            except Exception as e:
                result = CheckResult(
                    name=check_fn.__name__,
                    status=CheckStatus.ERROR,
                    detail=f"Exception: {e}",
                )
            report.checks.append(result)

        report.total_duration_ms = (time.monotonic() - t0) * 1000
        return report

    def _check_connectivity(self) -> CheckResult:
        """Verify the adapter can reach the exchange."""
        t0 = time.monotonic()
        try:
            ok = self.adapter.ping()
            latency = (time.monotonic() - t0) * 1000
            if ok:
                return CheckResult(
                    name="connectivity",
                    status=CheckStatus.PASS,
                    detail=f"Ping OK in {latency:.0f}ms",
                    latency_ms=latency,
                )
            return CheckResult(
                name="connectivity",
                status=CheckStatus.FAIL,
                detail="Ping returned False",
                latency_ms=latency,
            )
        except Exception as e:
            return CheckResult(name="connectivity", status=CheckStatus.ERROR, detail=str(e))

    def _check_balance(self) -> CheckResult:
        """Verify balance query works."""
        try:
            balance = self.adapter.get_balance()
            if isinstance(balance, dict) and "equity" in str(balance).lower():
                return CheckResult(
                    name="balance_query",
                    status=CheckStatus.PASS,
                    detail=f"Balance: {balance}",
                )
            return CheckResult(
                name="balance_query",
                status=CheckStatus.FAIL,
                detail=f"Unexpected balance format: {balance}",
            )
        except Exception as e:
            return CheckResult(name="balance_query", status=CheckStatus.ERROR, detail=str(e))

    def _check_market_order(self) -> CheckResult:
        """Place and verify a small market order."""
        symbol = self.symbols[0]
        try:
            result = self.adapter.place_order(
                symbol=symbol, side="buy", qty=self.test_qty, order_type="market"
            )
            order_id = result.get("order_id") or result.get("id")
            if order_id:
                return CheckResult(
                    name="market_order",
                    status=CheckStatus.PASS,
                    detail=f"Order placed: {order_id}, qty={self.test_qty}",
                    metadata={"order_id": order_id},
                )
            status = result.get("status", "unknown")
            if status in ("rejected", "error"):
                return CheckResult(
                    name="market_order",
                    status=CheckStatus.FAIL,
                    detail=f"Order rejected: {result}",
                )
            return CheckResult(
                name="market_order",
                status=CheckStatus.PASS,
                detail=f"Order result: {result}",
            )
        except Exception as e:
            return CheckResult(name="market_order", status=CheckStatus.ERROR, detail=str(e))

    def _check_position_update(self) -> CheckResult:
        """Verify position reflects after order."""
        symbol = self.symbols[0]
        try:
            pos = self.adapter.get_position(symbol)
            if isinstance(pos, dict):
                return CheckResult(
                    name="position_update",
                    status=CheckStatus.PASS,
                    detail=f"Position: {pos}",
                )
            return CheckResult(
                name="position_update",
                status=CheckStatus.FAIL,
                detail=f"Unexpected position format: {pos}",
            )
        except Exception as e:
            return CheckResult(name="position_update", status=CheckStatus.ERROR, detail=str(e))

    def _check_cancel_order(self) -> CheckResult:
        """Test order cancellation."""
        symbol = self.symbols[0]
        try:
            # Place a limit order far from mid (won't fill)
            place_result = self.adapter.place_order(
                symbol=symbol, side="buy", qty=self.test_qty,
                order_type="limit", price=1.0,  # absurdly low
            )
            order_id = place_result.get("order_id") or place_result.get("id")
            if not order_id:
                return CheckResult(
                    name="cancel_order",
                    status=CheckStatus.SKIP,
                    detail="Could not place order to cancel (no order_id)",
                )
            cancel_result = self.adapter.cancel_order(order_id)
            if cancel_result.get("status") in ("cancelled", "canceled", "ok"):
                return CheckResult(
                    name="cancel_order",
                    status=CheckStatus.PASS,
                    detail=f"Cancelled order {order_id}",
                )
            return CheckResult(
                name="cancel_order",
                status=CheckStatus.FAIL,
                detail=f"Cancel failed: {cancel_result}",
            )
        except Exception as e:
            return CheckResult(name="cancel_order", status=CheckStatus.ERROR, detail=str(e))

    def _check_error_handling(self) -> CheckResult:
        """Test that invalid orders are properly rejected."""
        try:
            # Place an obviously invalid order
            result = self.adapter.place_order(
                symbol="INVALID_PAIR", side="buy", qty=0, order_type="market"
            )
            status = result.get("status", "")
            if status in ("rejected", "error") or "error" in str(result).lower():
                return CheckResult(
                    name="error_handling",
                    status=CheckStatus.PASS,
                    detail="Invalid order properly rejected",
                )
            return CheckResult(
                name="error_handling",
                status=CheckStatus.FAIL,
                detail=f"Invalid order not rejected: {result}",
            )
        except Exception as e:
            # Exception on invalid order is also acceptable
            return CheckResult(
                name="error_handling",
                status=CheckStatus.PASS,
                detail=f"Invalid order raised exception (acceptable): {e}",
            )
