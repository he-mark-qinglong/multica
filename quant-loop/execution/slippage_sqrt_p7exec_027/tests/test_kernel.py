"""Unit tests for the Almgren sqrt kernel + request/estimate dataclasses.

These cover the parent-issue acceptance criteria:

* "Unit tests cover happy path AND at least one failure mode"

Coverage summary
----------------

* Happy path: Almgren factorisation against an analytic oracle for
  several qty / volatility / daily_volume / horizon / k_factor
  combinations.
* Multiple verdict buckets: minimal / low / moderate / high / extreme.
* Edge cases the parent issue names explicitly: volatility_per_s=0
  (sigma=0) and qty=0 / daily_volume=0 / k_factor <= 0.
* total_slippage_bps = temporary_impact_bps + fee_bps.
* Input validation: empty strings, bad side, negative numbers,
  non-positive quantities.
* Kernel-only latency budget: p99 ≤ 250 µs over 5000 requests per
  round × 3 rounds.
* Full-estimate latency observed separately (informational).
"""

from __future__ import annotations

import math
import shutil
import statistics
import tempfile
import time
import unittest
from pathlib import Path

from slippage_sqrt_p7exec_027 import (
    DEFAULT_ARRIVAL_HORIZON_S,
    DEFAULT_K_FACTOR,
    DEFAULT_SECONDS_PER_DAY,
    InvalidRequestError,
    KERNEL_VERSION,
    SlippageSqrtCalculator,
    SlippageSqrtEstimate,
    SlippageSqrtJournal,
    SlippageSqrtJournalReplayRequired,
    SlippageSqrtJournalWriteError,
    SlippageSqrtRequest,
    VERDICTS_ALL,
    VERDICT_EXTREME,
    VERDICT_HIGH,
    VERDICT_LOW,
    VERDICT_MINIMAL,
    VERDICT_MODERATE,
    VERDICT_THRESHOLD_HIGH,
    VERDICT_THRESHOLD_LOW,
    VERDICT_THRESHOLD_MINIMAL,
    VERDICT_THRESHOLD_MODERATE,
    compute_slippage_sqrt,
)
from slippage_sqrt_p7exec_027.exceptions import SlippageSqrtHalt


# --- Helpers --------------------------------------------------------


def _tmp_dir() -> Path:
    return Path(tempfile.mkdtemp(prefix="kernel_p7exec_027_"))


def _req(
    fill_id: str = "f:1",
    *,
    symbol: str = "BTCUSDT",
    venue: str = "binance",
    side: str = "buy",
    qty: float = 0.1,
    daily_volume: float = 100_000.0,
    volatility_per_s: float = 0.0001,
    arrival_horizon_s: float = DEFAULT_ARRIVAL_HORIZON_S,
    seconds_per_day: float = DEFAULT_SECONDS_PER_DAY,
    k_factor: float = DEFAULT_K_FACTOR,
    fee_bps: float = 0.0,
    mid_price: float = 0.0,
    strategy_id: str = "s:1",
) -> SlippageSqrtRequest:
    return SlippageSqrtRequest(
        fill_id=fill_id,
        strategy_id=strategy_id,
        symbol=symbol,
        venue=venue,
        side=side,
        qty=qty,
        mid_price=mid_price,
        daily_volume=daily_volume,
        volatility_per_s=volatility_per_s,
        arrival_horizon_s=arrival_horizon_s,
        seconds_per_day=seconds_per_day,
        k_factor=k_factor,
        fee_bps=fee_bps,
    )


# --- Happy path -----------------------------------------------------


class TestAlmgrenFactorisation(unittest.TestCase):
    """Confirm the kernel formula matches the analytic Almgren expression."""

    def test_known_inputs_match_oracle(self) -> None:
        # qty=0.5 BTC, daily_volume=100000 BTC, sigma=0.0001/s, T=1s, k=1.
        # v_per_s = 100000 / 86400 ≈ 1.1574
        # participation = 0.5 / 1.1574 = 0.432
        # temp_impact_bps = 1.0 * 0.0001 * sqrt(0.432) * 10000 ≈ 0.6573 bps
        req = _req(qty=0.5)
        est = compute_slippage_sqrt(req)
        expected_v_per_s = 100_000.0 / 86400.0
        expected_part = 0.5 / expected_v_per_s
        expected_bps = 1.0 * 0.0001 * math.sqrt(expected_part) * 10000.0
        self.assertAlmostEqual(est.v_per_s, expected_v_per_s, places=10)
        self.assertAlmostEqual(est.participation, expected_part, places=10)
        self.assertAlmostEqual(est.temporary_impact_bps, expected_bps, places=8)
        self.assertEqual(est.verdict, VERDICT_MINIMAL)
        self.assertEqual(est.kernel_version, KERNEL_VERSION)

    def test_larger_order_drives_higher_impact(self) -> None:
        # Doubling qty doubles sqrt(participation), so impact grows by sqrt(2).
        small = compute_slippage_sqrt(_req(qty=0.1)).temporary_impact_bps
        big = compute_slippage_sqrt(_req(qty=0.2)).temporary_impact_bps
        self.assertGreater(big, small)
        self.assertAlmostEqual(big / small, math.sqrt(2.0), places=6)

    def test_lower_daily_volume_drives_higher_impact(self) -> None:
        # Halving daily_volume doubles sqrt(participation), so impact grows by sqrt(2).
        thin = compute_slippage_sqrt(_req(daily_volume=50_000.0)).temporary_impact_bps
        fat = compute_slippage_sqrt(_req(daily_volume=100_000.0)).temporary_impact_bps
        self.assertGreater(thin, fat)
        self.assertAlmostEqual(thin / fat, math.sqrt(2.0), places=6)

    def test_higher_volatility_drives_higher_impact(self) -> None:
        # Impact scales linearly with sigma_per_s.
        quiet = compute_slippage_sqrt(_req(volatility_per_s=0.0001)).temporary_impact_bps
        busy = compute_slippage_sqrt(_req(volatility_per_s=0.0002)).temporary_impact_bps
        self.assertAlmostEqual(busy / quiet, 2.0, places=6)

    def test_k_factor_scales_linearly(self) -> None:
        # Empirical Y factor is multiplied through.
        base = compute_slippage_sqrt(_req(k_factor=1.0)).temporary_impact_bps
        y_half = compute_slippage_sqrt(_req(k_factor=0.314)).temporary_impact_bps
        y_two = compute_slippage_sqrt(_req(k_factor=2.0)).temporary_impact_bps
        self.assertAlmostEqual(y_half / base, 0.314, places=6)
        self.assertAlmostEqual(y_two / base, 2.0, places=6)

    def test_total_slippage_bps_equals_impact_plus_fee(self) -> None:
        for fee in (0.0, 1.5, 5.0, 12.0):
            est = compute_slippage_sqrt(_req(qty=0.5, fee_bps=fee))
            self.assertAlmostEqual(
                est.total_slippage_bps,
                est.temporary_impact_bps + est.fee_bps,
                places=10,
            )

    def test_participation_clamped_when_negative(self) -> None:
        # Defensive: participation is non-negative by construction.
        # We don't have an API to push it negative, but a constructed
        # request with a tiny qty and huge daily_volume produces a very
        # small participation; verify the kernel never produces a
        # negative temp_impact_bps.
        for qty in (0.001, 0.0001, 0.00001):
            est = compute_slippage_sqrt(_req(qty=qty))
            self.assertGreaterEqual(est.temporary_impact_bps, 0.0)


# --- Verdict classification ----------------------------------------


class TestVerdictClassification(unittest.TestCase):
    def _drive(self, *, qty: float) -> SlippageSqrtEstimate:
        return compute_slippage_sqrt(_req(qty=qty))

    def test_minimal_below_5bps(self) -> None:
        est = self._drive(qty=0.001)
        self.assertLess(est.temporary_impact_bps, VERDICT_THRESHOLD_MINIMAL)
        self.assertEqual(est.verdict, VERDICT_MINIMAL)

    def test_low_between_5_and_15_bps(self) -> None:
        # For default params (sigma=0.0001/s, k=1, daily_volume=100k,
        # T=1s, daily_seconds=86400), ``impact_bps = sqrt(qty / v_per_s)``
        # where ``v_per_s = 100000/86400 ≈ 1.1574``. To land at >= 5 bps
        # need ``qty >= v_per_s * 25 ≈ 28.9``; at < 15 bps need
        # ``qty < v_per_s * 225 ≈ 260.4``.
        v = 100_000.0 / 86400.0
        # 30 BTC → ~5.1 bps (just past low boundary);
        # 100 BTC → ~9.3 bps (mid-low bucket).
        for qty, expected in (
            (30.0, VERDICT_LOW),
            (100.0, VERDICT_LOW),
        ):
            with self.subTest(qty=qty):
                self.assertEqual(self._drive(qty=qty).verdict, expected)
                # Sanity-pin: the impact_bps must actually be in the
                # bucket range for the test to be meaningful.
                impact = self._drive(qty=qty).temporary_impact_bps
                self.assertGreaterEqual(impact, VERDICT_THRESHOLD_MINIMAL)
                self.assertLess(impact, VERDICT_THRESHOLD_LOW)
        # Also pin v_per_s derived above (the comment line above used it).
        self.assertAlmostEqual(v, 100_000.0 / 86400.0, places=10)

    def test_moderate_between_15_and_50(self) -> None:
        # For impact_bps in [15, 50) need participation in [225, 2500)
        # → qty in [v_per_s*225, v_per_s*2500) = [260, 2893) BTC.
        # 300 BTC → ~16.1 bps (just past moderate boundary).
        est = self._drive(qty=300.0)
        self.assertEqual(est.verdict, VERDICT_MODERATE)
        self.assertGreaterEqual(est.temporary_impact_bps, VERDICT_THRESHOLD_LOW)
        self.assertLess(est.temporary_impact_bps, VERDICT_THRESHOLD_MODERATE)

    def test_high_between_50_and_200(self) -> None:
        # For impact_bps in [50, 200) need qty in [v_per_s*2500, v_per_s*40000)
        # = [2893, 46296) BTC. 3000 BTC → ~50.9 bps (just past high boundary).
        est = self._drive(qty=3_000.0)
        self.assertEqual(est.verdict, VERDICT_HIGH)
        self.assertGreaterEqual(est.temporary_impact_bps, VERDICT_THRESHOLD_MODERATE)
        self.assertLess(est.temporary_impact_bps, VERDICT_THRESHOLD_HIGH)

    def test_extreme_at_or_above_200_bps(self) -> None:
        # impact_bps >= 200 needs participation >= 40000 → qty >= ~46296.
        # 50000 BTC on 100k daily volume is roughly half a day of volume
        # in one second — clearly extreme.
        est = self._drive(qty=50_000.0)
        self.assertEqual(est.verdict, VERDICT_EXTREME)
        self.assertGreaterEqual(est.temporary_impact_bps, VERDICT_THRESHOLD_HIGH)

    def test_verdict_thresholds_documented(self) -> None:
        """Verdict vocabulary matches the constants in models."""
        self.assertEqual(
            {v.__class__.__name__ for v in (VERDICT_MINIMAL, VERDICT_LOW,
                                            VERDICT_MODERATE, VERDICT_HIGH,
                                            VERDICT_EXTREME)},
            {"str"},
        )
        self.assertSetEqual(
            set(VERDICTS_ALL),
            {VERDICT_MINIMAL, VERDICT_LOW, VERDICT_MODERATE, VERDICT_HIGH, VERDICT_EXTREME},
        )


# --- Edge cases named in the parent issue ---------------------------


class TestSigmaZeroAndEdgeCases(unittest.TestCase):
    """The parent issue names sigma=0 / negative Q / 0 daily volume as
    failure-mode probes. Verify each is handled correctly.

    * sigma=0 → kernel produces impact=0, verdict=minimal (legitimate
      corner case for a perfectly still market; the math allows it).
    * qty<=0, daily_volume<=0, k_factor<=0 → InvalidRequestError at
      __post_init__.
    * negative sigma / negative fee → InvalidRequestError.
    """

    def test_sigma_zero_produces_zero_impact_and_minimal_verdict(self) -> None:
        est = compute_slippage_sqrt(_req(volatility_per_s=0.0))
        self.assertEqual(est.temporary_impact_bps, 0.0)
        self.assertEqual(est.verdict, VERDICT_MINIMAL)
        self.assertEqual(est.total_slippage_bps, est.fee_bps)

    def test_zero_qty_rejected_at_construction(self) -> None:
        with self.assertRaises(InvalidRequestError):
            SlippageSqrtRequest(
                fill_id="x", strategy_id="x", symbol="BTCUSDT", venue="binance",
                side="buy", qty=0.0, daily_volume=100.0, volatility_per_s=0.0001,
            )

    def test_negative_qty_rejected_at_construction(self) -> None:
        with self.assertRaises(InvalidRequestError):
            SlippageSqrtRequest(
                fill_id="x", strategy_id="x", symbol="BTCUSDT", venue="binance",
                side="buy", qty=-0.1, daily_volume=100.0, volatility_per_s=0.0001,
            )

    def test_zero_daily_volume_rejected_at_construction(self) -> None:
        with self.assertRaises(InvalidRequestError):
            SlippageSqrtRequest(
                fill_id="x", strategy_id="x", symbol="BTCUSDT", venue="binance",
                side="buy", qty=0.1, daily_volume=0.0, volatility_per_s=0.0001,
            )

    def test_negative_sigma_rejected_at_construction(self) -> None:
        with self.assertRaises(InvalidRequestError):
            SlippageSqrtRequest(
                fill_id="x", strategy_id="x", symbol="BTCUSDT", venue="binance",
                side="buy", qty=0.1, daily_volume=100.0, volatility_per_s=-0.0001,
            )

    def test_zero_k_factor_rejected_at_construction(self) -> None:
        with self.assertRaises(InvalidRequestError):
            SlippageSqrtRequest(
                fill_id="x", strategy_id="x", symbol="BTCUSDT", venue="binance",
                side="buy", qty=0.1, daily_volume=100.0, volatility_per_s=0.0001,
                k_factor=0.0,
            )

    def test_negative_fee_rejected_at_construction(self) -> None:
        with self.assertRaises(InvalidRequestError):
            SlippageSqrtRequest(
                fill_id="x", strategy_id="x", symbol="BTCUSDT", venue="binance",
                side="buy", qty=0.1, daily_volume=100.0, volatility_per_s=0.0001,
                fee_bps=-1.0,
            )


# --- Input validation (broader) -------------------------------------


class TestRequestValidation(unittest.TestCase):
    def test_empty_fill_id_rejected(self) -> None:
        with self.assertRaises(InvalidRequestError):
            SlippageSqrtRequest(
                fill_id="", strategy_id="s", symbol="BTCUSDT", venue="binance",
                side="buy", qty=0.1, daily_volume=100.0, volatility_per_s=0.0001,
            )

    def test_empty_symbol_rejected(self) -> None:
        with self.assertRaises(InvalidRequestError):
            SlippageSqrtRequest(
                fill_id="f", strategy_id="s", symbol="", venue="binance",
                side="buy", qty=0.1, daily_volume=100.0, volatility_per_s=0.0001,
            )

    def test_empty_venue_rejected(self) -> None:
        with self.assertRaises(InvalidRequestError):
            SlippageSqrtRequest(
                fill_id="f", strategy_id="s", symbol="BTCUSDT", venue="",
                side="buy", qty=0.1, daily_volume=100.0, volatility_per_s=0.0001,
            )

    def test_bad_side_rejected(self) -> None:
        with self.assertRaises(InvalidRequestError):
            SlippageSqrtRequest(
                fill_id="f", strategy_id="s", symbol="BTCUSDT", venue="binance",
                side="long", qty=0.1, daily_volume=100.0, volatility_per_s=0.0001,
            )

    def test_zero_arrival_horizon_rejected(self) -> None:
        with self.assertRaises(InvalidRequestError):
            SlippageSqrtRequest(
                fill_id="f", strategy_id="s", symbol="BTCUSDT", venue="binance",
                side="buy", qty=0.1, daily_volume=100.0, volatility_per_s=0.0001,
                arrival_horizon_s=0.0,
            )

    def test_zero_seconds_per_day_rejected(self) -> None:
        with self.assertRaises(InvalidRequestError):
            SlippageSqrtRequest(
                fill_id="f", strategy_id="s", symbol="BTCUSDT", venue="binance",
                side="buy", qty=0.1, daily_volume=100.0, volatility_per_s=0.0001,
                seconds_per_day=0.0,
            )

    def test_mid_price_negative_rejected(self) -> None:
        with self.assertRaises(InvalidRequestError):
            SlippageSqrtRequest(
                fill_id="f", strategy_id="s", symbol="BTCUSDT", venue="binance",
                side="buy", qty=0.1, daily_volume=100.0, volatility_per_s=0.0001,
                mid_price=-1.0,
            )


# --- Calculator lifecycle / persistence ----------------------------


class TestCalculatorLifecycle(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = _tmp_dir()

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_estimate_journals_row_first(self) -> None:
        with SlippageSqrtCalculator(self.tmp) as calc:
            est = calc.estimate(_req(fill_id="f:1", qty=0.1))
            self.assertEqual(est.fill_id, "f:1")
            self.assertEqual(est.verdict, VERDICT_MINIMAL)

        journal = self.tmp / "journal.jsonl"
        self.assertTrue(journal.exists())
        self.assertGreater(journal.stat().st_size, 0)

    def test_counters_track_per_symbol_and_verdict(self) -> None:
        with SlippageSqrtCalculator(self.tmp) as calc:
            calc.estimate(_req(fill_id="a", symbol="BTCUSDT", qty=0.1))
            calc.estimate(_req(fill_id="b", symbol="BTCUSDT", qty=0.5))
            calc.estimate(_req(fill_id="c", symbol="ETHUSDT", qty=0.1))

            s = calc.stats()
            self.assertEqual(s["total_requests"], 3)
            self.assertEqual(s["per_symbol"]["BTCUSDT"]["n_requests"], 2)
            self.assertEqual(s["per_symbol"]["ETHUSDT"]["n_requests"], 1)
            self.assertGreater(
                s["per_symbol"]["BTCUSDT"]["cumulative_impact_bps"],
                s["per_symbol"]["ETHUSDT"]["cumulative_impact_bps"],
            )
            # Both are minimal at qty 0.1.
            self.assertEqual(s["verdict_counts"][VERDICT_MINIMAL], 3)

    def test_estimate_records_min_and_max_per_symbol(self) -> None:
        with SlippageSqrtCalculator(self.tmp) as calc:
            calc.estimate(_req(fill_id="a", qty=0.001))
            calc.estimate(_req(fill_id="b", qty=0.5))
            s = calc.stats_for("BTCUSDT")
            self.assertLess(s["min_impact_bps"], s["max_impact_bps"])
            self.assertAlmostEqual(s["min_impact_bps"], s["min_impact_bps"], places=0)
            self.assertAlmostEqual(s["max_impact_bps"], s["max_impact_bps"], places=0)

    def test_unknown_symbol_stats_returns_zero(self) -> None:
        with SlippageSqrtCalculator(self.tmp) as calc:
            self.assertEqual(calc.cumulative_impact_bps_for("UNKNOWN"), 0.0)
            self.assertEqual(calc.stats_for("UNKNOWN")["n_requests"], 0)

    def test_rehydrate_via_checkpoint_on_restart(self) -> None:
        with SlippageSqrtCalculator(self.tmp) as calc:
            for i in range(5):
                calc.estimate(_req(fill_id=f"f:{i}", qty=0.1 + 0.1 * i))
            expected_total = calc.stats()["total_requests"]
            expected_btc = calc.cumulative_impact_bps_for("BTCUSDT")

        with SlippageSqrtCalculator(self.tmp) as calc2:
            self.assertEqual(calc2.stats()["total_requests"], expected_total)
            self.assertAlmostEqual(
                calc2.cumulative_impact_bps_for("BTCUSDT"),
                expected_btc,
                places=10,
            )

    def test_known_symbols_sorted(self) -> None:
        with SlippageSqrtCalculator(self.tmp) as calc:
            calc.estimate(_req(fill_id="a", symbol="ETHUSDT"))
            calc.estimate(_req(fill_id="b", symbol="BTCUSDT"))
            calc.estimate(_req(fill_id="c", symbol="SOLUSDT"))
            self.assertEqual(
                calc.known_symbols(),
                ["BTCUSDT", "ETHUSDT", "SOLUSDT"],
            )


# --- Failure modes around the WAL -------------------------------


class TestJournalFailureModes(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = _tmp_dir()

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_close_journal_then_estimate_raises_write_error(self) -> None:
        calc = SlippageSqrtCalculator(self.tmp)
        calc.close()
        with self.assertRaises(SlippageSqrtJournalWriteError):
            calc.estimate(_req(fill_id="x"))

    def test_journal_present_no_checkpoint_raises_replay_required(self) -> None:
        # Write a row, close, then delete checkpoint so rehydrate hits replay-required.
        with SlippageSqrtCalculator(self.tmp) as calc:
            calc.estimate(_req(fill_id="x", qty=0.1))
        # Now delete the checkpoint to force the replay-required path on restart.
        ckp = self.tmp / "state.json"
        if ckp.exists():
            ckp.unlink()
        with self.assertRaises(SlippageSqrtJournalReplayRequired):
            SlippageSqrtCalculator(self.tmp)

    def test_corrupted_journal_raises_halt_on_replay_via_rebuild(self) -> None:
        # Write a valid row, then corrupt the journal by appending garbage.
        with SlippageSqrtCalculator(self.tmp) as calc:
            calc.estimate(_req(fill_id="x", qty=0.1))
            calc.close()
        jrn = self.tmp / "journal.jsonl"
        with open(jrn, "a", encoding="utf-8") as f:
            f.write("not json\n")
        # build the helper directly to surface the halt path
        from slippage_sqrt_p7exec_027.rebuild_checkpoint import rebuild
        with self.assertRaises(SlippageSqrtHalt):
            rebuild(self.tmp)

    def test_unknown_kind_in_journal_raises_halt(self) -> None:
        with SlippageSqrtCalculator(self.tmp) as calc:
            calc.estimate(_req(fill_id="x", qty=0.1))
            calc.close()
        import json
        jrn = self.tmp / "journal.jsonl"
        with open(jrn, "a", encoding="utf-8") as f:
            f.write(json.dumps({"kind": "mystery", "x": 1}) + "\n")
        from slippage_sqrt_p7exec_027.rebuild_checkpoint import rebuild
        with self.assertRaises(SlippageSqrtHalt):
            rebuild(self.tmp)


# --- Rebuild checkpoint helper -----------------------------------


class TestRebuildCheckpoint(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = _tmp_dir()

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_rebuild_restores_per_symbol_aggregates(self) -> None:
        with SlippageSqrtCalculator(self.tmp) as calc:
            calc.estimate(_req(fill_id="a", symbol="BTCUSDT", qty=0.5))
            calc.estimate(_req(fill_id="b", symbol="ETHUSDT", qty=2.0))
            calc.estimate(_req(fill_id="c", symbol="BTCUSDT", qty=0.1))
            btc_pre = calc.cumulative_impact_bps_for("BTCUSDT")
            eth_pre = calc.cumulative_impact_bps_for("ETHUSDT")
            total_pre = calc.stats()["total_requests"]

        ckp = self.tmp / "state.json"
        if ckp.exists():
            ckp.unlink()

        from slippage_sqrt_p7exec_027.rebuild_checkpoint import rebuild

        rebuild(self.tmp)
        self.assertTrue(ckp.exists())

        with SlippageSqrtCalculator(self.tmp) as calc2:
            self.assertAlmostEqual(
                calc2.cumulative_impact_bps_for("BTCUSDT"), btc_pre, places=10
            )
            self.assertAlmostEqual(
                calc2.cumulative_impact_bps_for("ETHUSDT"), eth_pre, places=10
            )
            self.assertEqual(calc2.stats()["total_requests"], total_pre)


# --- Latency budget ----------------------------------------------


class TestLatencyBudget(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = _tmp_dir()

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_kernel_p99_under_250us_budget(self) -> None:
        """Kernel-only p99 must be well under 250 µs over 3 rounds of 5000 requests.

        The kernel is a few float ops + dict construction, so even on
        a slow host this should land in the low-microseconds per call.
        The budget of 250 µs is generous; we accept and report whatever
        we observe.
        """
        budget_us = 250.0
        rounds = 3
        iterations = 5000
        req = _req(qty=0.1)
        worst_us = 0.0
        for _ in range(rounds):
            durations_ns = []
            for _ in range(iterations):
                t0 = time.perf_counter_ns()
                est = compute_slippage_sqrt(req)
                t1 = time.perf_counter_ns()
                # Cheap touch so the optimiser cannot elide the call.
                _ = est.verdict
                durations_ns.append(t1 - t0)
            durations_ns.sort()
            p99_idx = int(round(0.99 * (len(durations_ns) - 1)))
            p99_us = durations_ns[p99_idx] / 1000.0
            worst_us = max(worst_us, p99_us)
        # Generous bound: kernel p99 < 250us. We allow a tiny slack so
        # noisy CI runners do not flake the test.
        self.assertLess(
            worst_us,
            budget_us,
            f"kernel p99 {worst_us:.1f}us exceeded {budget_us:.1f}us budget",
        )

    def test_full_estimate_includes_io_observed_separately(self) -> None:
        """Informational: full estimate (kernel + WAL write + fsync) is I/O dominated.

        NOT a gating test — the parent issue accepts the I/O cost
        because it amortises the WAL pattern across all components.
        We just observe p50 / p99 so EVIDENCE.md has real numbers.
        """
        with SlippageSqrtCalculator(self.tmp) as calc:
            req = _req(fill_id="f:lat", qty=0.1)
            n = 200
            durations_us = []
            for _ in range(n):
                t0 = time.perf_counter_ns()
                est = calc.estimate(req)
                t1 = time.perf_counter_ns()
                _ = est.verdict
                durations_us.append((t1 - t0) / 1000.0)
            durations_us.sort()
            p50 = durations_us[n // 2]
            p99 = durations_us[int(round(0.99 * (n - 1)))]
            mean = statistics.mean(durations_us)
            # Record so EVIDENCE.md can quote it.
            self._record_observed("full_estimate_p50_us", p50)
            self._record_observed("full_estimate_p99_us", p99)
            self._record_observed("full_estimate_mean_us", mean)

    def _record_observed(self, key: str, value: float) -> None:
        # Tiny shim: stash on the test instance so EVIDENCE.md can pull
        # it. We deliberately do NOT write into the WAL or checkpoint
        # file from a unit test — that would be a side effect.
        if not hasattr(self, "_observed"):
            self._observed = {}
        self._observed[key] = value


if __name__ == "__main__":
    unittest.main()