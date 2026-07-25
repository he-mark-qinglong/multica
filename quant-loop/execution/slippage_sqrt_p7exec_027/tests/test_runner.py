"""Unit tests for the slippage_sqrt ExecutionRunner wrapper.

These cover the high-level wrapper behaviour that sits on top of the
kernel / calculator: stats exposure, shutdown semantics, idempotency,
and per-symbol read views.
"""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from slippage_sqrt_p7exec_027 import (
    ExecutionRunner,
    InvalidRequestError,
    KERNEL_VERSION,
    SlippageSqrtRequest,
    VERDICT_MINIMAL,
    build_runner,
)


def _tmp_dir() -> Path:
    return Path(tempfile.mkdtemp(prefix="runner_p7exec_027_"))


def _req(fill_id: str, *, symbol: str = "BTCUSDT", qty: float = 0.1) -> SlippageSqrtRequest:
    return SlippageSqrtRequest(
        fill_id=fill_id,
        strategy_id="s:1",
        symbol=symbol,
        venue="binance",
        side="buy",
        qty=qty,
        mid_price=0.0,
        daily_volume=100_000.0,
        volatility_per_s=0.0001,
        fee_bps=0.0,
    )


class TestExecutionRunner(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = _tmp_dir()

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_estimate_returns_estimate(self) -> None:
        with ExecutionRunner(self.tmp) as r:
            est = r.estimate(_req("f:1"))
            self.assertEqual(est.fill_id, "f:1")
            self.assertEqual(est.verdict, VERDICT_MINIMAL)

    def test_stats_exposes_total_and_verdict_counts(self) -> None:
        with ExecutionRunner(self.tmp) as r:
            r.estimate(_req("a"))
            r.estimate(_req("b"))
            s = r.stats()
            self.assertEqual(s["total_requests"], 2)
            self.assertEqual(s["verdict_counts"][VERDICT_MINIMAL], 2)
            self.assertEqual(s["kernel_version"], KERNEL_VERSION)

    def test_known_symbols_after_estimate(self) -> None:
        with ExecutionRunner(self.tmp) as r:
            r.estimate(_req("a", symbol="BTCUSDT"))
            r.estimate(_req("b", symbol="ETHUSDT"))
            self.assertEqual(r.known_symbols(), ["BTCUSDT", "ETHUSDT"])

    def test_stats_for_unknown_symbol_is_zero(self) -> None:
        with ExecutionRunner(self.tmp) as r:
            self.assertEqual(r.stats_for("UNKNOWN")["n_requests"], 0)
            self.assertEqual(r.cumulative_impact_bps_for("UNKNOWN"), 0.0)

    def test_cumulative_impact_bps_for_tracks_symbol(self) -> None:
        with ExecutionRunner(self.tmp) as r:
            r.estimate(_req("a", symbol="BTCUSDT", qty=0.1))
            r.estimate(_req("b", symbol="BTCUSDT", qty=0.2))
            r.estimate(_req("c", symbol="ETHUSDT", qty=0.1))
            btc = r.cumulative_impact_bps_for("BTCUSDT")
            eth = r.cumulative_impact_bps_for("ETHUSDT")
            self.assertGreater(btc, eth)
            self.assertGreater(btc, 0.0)

    def test_calculator_returns_underlying(self) -> None:
        with ExecutionRunner(self.tmp) as r:
            self.assertEqual(r.calculator().kernel_version(), KERNEL_VERSION)

    def test_kernel_version(self) -> None:
        with ExecutionRunner(self.tmp) as r:
            self.assertEqual(r.kernel_version(), KERNEL_VERSION)

    def test_shutdown_makes_subsequent_estimate_raise(self) -> None:
        r = ExecutionRunner(self.tmp)
        r.shutdown()
        with self.assertRaises(RuntimeError):
            r.estimate(_req("x"))

    def test_stats_after_shutdown_returns_empty(self) -> None:
        r = ExecutionRunner(self.tmp)
        r.shutdown()
        self.assertEqual(r.stats(), {})

    def test_invalid_request_propagates(self) -> None:
        with ExecutionRunner(self.tmp) as r:
            with self.assertRaises(InvalidRequestError):
                r.estimate(
                    SlippageSqrtRequest(
                        fill_id="bad", strategy_id="s", symbol="BTCUSDT", venue="binance",
                        side="buy", qty=-1.0, daily_volume=100.0, volatility_per_s=0.0001,
                    )
                )

    def test_build_runner_factory(self) -> None:
        r = build_runner(self.tmp)
        try:
            r.estimate(_req("f:1"))
            self.assertEqual(r.stats()["total_requests"], 1)
        finally:
            r.shutdown()

    def test_context_manager_invokes_shutdown(self) -> None:
        with ExecutionRunner(self.tmp) as r:
            r.estimate(_req("x"))
            self.assertEqual(r.stats()["total_requests"], 1)
        # After context manager exit, stats should be empty (shutdown ran).
        # We re-open the runner to read the journal.
        with ExecutionRunner(self.tmp) as r2:
            self.assertEqual(r2.stats()["total_requests"], 1)


if __name__ == "__main__":
    unittest.main()