"""Tests for testnet validation framework."""
import pytest
from unittest.mock import MagicMock

from _shared.execution.testnet_validator import (
    TestnetValidator, CheckStatus, CheckResult, ValidationReport,
)


class _MockAdapter:
    """Mock exchange adapter for testing."""
    def __init__(self, fail_ping=False, fail_balance=False, reject_orders=False):
        self.fail_ping = fail_ping
        self.fail_balance = fail_balance
        self.reject_orders = reject_orders
        self._orders = {}
        self._next_id = 1

    def ping(self):
        return not self.fail_ping

    def get_balance(self):
        if self.fail_balance:
            return None
        return {"equity": 10000.0, "available": 8000.0}

    def place_order(self, symbol, side, qty, order_type="market", price=None):
        if self.reject_orders or (symbol == "INVALID_PAIR"):
            return {"status": "rejected", "error": "invalid symbol"}
        if qty <= 0:
            return {"status": "rejected", "error": "qty must be positive"}
        oid = str(self._next_id)
        self._next_id += 1
        self._orders[oid] = {"symbol": symbol, "side": side, "qty": qty, "status": "filled"}
        return {"order_id": oid, "status": "filled", "symbol": symbol}

    def cancel_order(self, order_id):
        if order_id in self._orders:
            self._orders[order_id]["status"] = "cancelled"
            return {"status": "cancelled"}
        return {"status": "error", "error": "order not found"}

    def get_position(self, symbol):
        return {"symbol": symbol, "qty": 0.001, "entry_price": 50000}


class TestConnectivity:
    def test_pass_on_success(self):
        v = TestnetValidator(adapter=_MockAdapter())
        r = v._check_connectivity()
        assert r.status == CheckStatus.PASS
        assert r.latency_ms > 0

    def test_fail_on_ping_failure(self):
        v = TestnetValidator(adapter=_MockAdapter(fail_ping=True))
        r = v._check_connectivity()
        assert r.status == CheckStatus.FAIL


class TestBalanceQuery:
    def test_pass_with_equity(self):
        v = TestnetValidator(adapter=_MockAdapter())
        r = v._check_balance()
        assert r.status == CheckStatus.PASS

    def test_fail_on_none(self):
        v = TestnetValidator(adapter=_MockAdapter(fail_balance=True))
        r = v._check_balance()
        assert r.status == CheckStatus.FAIL


class TestMarketOrder:
    def test_pass_on_fill(self):
        v = TestnetValidator(adapter=_MockAdapter())
        r = v._check_market_order()
        assert r.status == CheckStatus.PASS
        assert "order_id" in r.metadata

    def test_fail_on_reject(self):
        v = TestnetValidator(adapter=_MockAdapter(reject_orders=True))
        r = v._check_market_order()
        assert r.status == CheckStatus.FAIL


class TestFullSuite:
    def test_all_pass_with_healthy_adapter(self):
        v = TestnetValidator(adapter=_MockAdapter())
        report = v.run_all()
        assert report.all_passed
        assert len(report.failures) == 0
        assert len(report.passes) >= 5

    def test_failures_with_broken_adapter(self):
        v = TestnetValidator(adapter=_MockAdapter(fail_ping=True))
        report = v.run_all()
        assert not report.all_passed
        assert len(report.failures) >= 1

    def test_summary_string(self):
        v = TestnetValidator(adapter=_MockAdapter())
        report = v.run_all()
        s = report.summary()
        assert "pass" in s
        assert "ALL PASS" in s


class TestErrorHandling:
    def test_invalid_order_rejected(self):
        v = TestnetValidator(adapter=_MockAdapter())
        r = v._check_error_handling()
        assert r.status == CheckStatus.PASS

    def test_exception_on_invalid_is_acceptable(self):
        class _ExplodingAdapter(_MockAdapter):
            def place_order(self, *args, **kwargs):
                if args[0] == "INVALID_PAIR":
                    raise ValueError("invalid symbol")
                return super().place_order(*args, **kwargs)

        v = TestnetValidator(adapter=_ExplodingAdapter())
        r = v._check_error_handling()
        assert r.status == CheckStatus.PASS


class TestValidationReport:
    def test_all_passed_property(self):
        report = ValidationReport()
        report.checks = [
            CheckResult(name="a", status=CheckStatus.PASS),
            CheckResult(name="b", status=CheckStatus.PASS),
        ]
        assert report.all_passed

    def test_failures_property(self):
        report = ValidationReport()
        report.checks = [
            CheckResult(name="a", status=CheckStatus.PASS),
            CheckResult(name="b", status=CheckStatus.FAIL, detail="broken"),
        ]
        assert len(report.failures) == 1
        assert report.failures[0].name == "b"
