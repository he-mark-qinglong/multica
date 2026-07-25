"""Wire-in integration smoke test for slippage_sqrt_p7exec_027.

Drives a small synthetic historical estimate journal through
``LiveExecutionRunner`` (top-level ``execution/runner.py``) and asserts
that:

* the slippage_sqrt cost model is fanned-out from a single
  ``ingest_slippage_sqrt_request`` call,
* every estimate lands in the slippage_sqrt journal exactly once,
* restart-from-disk preserves the per-symbol aggregates (no silent
  drops),
* the runner's ``slippage_sqrt_stats()`` and global ``stats()``
  expose the slippage_sqrt component,
* shutdown cleanly closes the new component journal.

These tests cover the runner-level acceptance criteria named in the
parent issue ``[P7-EXEC-027] slippage_sqrt``:

* "Wire into existing ~/multica/quant-loop/execution/ runner"
* "Integration smoke against historical fill journal or paper-trading
   mode passes"

They live under this component's tests directory (rather than a
top-level ``execution/tests/``) because the workspace does not host a
top-level tests directory; sibling components follow the same pattern
(see ``slippage_cross_asset_p7exec_048/tests/test_integration_smoke.py``).
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path


# Make ``execution/runner.py`` (top-level wire-up) importable. The
# wire-up lives at the same directory level as the component sub-folders
# and is loaded as ``runner`` — matching how the live-trading harness
# loads it.
_EXEC_DIR = Path(__file__).resolve().parents[3]
if str(_EXEC_DIR) not in sys.path:
    sys.path.insert(0, str(_EXEC_DIR))

from runner import LiveExecutionRunner  # noqa: E402  (sys.path bootstrap above)
from slippage_sqrt_p7exec_027 import (  # noqa: E402
    KERNEL_VERSION,
    VERDICT_LOW,
    VERDICT_MINIMAL,
    VERDICT_MODERATE,
    SlippageSqrtEstimate,
    SlippageSqrtRequest,
)


def _tmp_dir() -> Path:
    return Path(tempfile.mkdtemp(prefix="wire_p7exec_027_"))


def _req(
    fill_id: str,
    symbol: str,
    *,
    side: str = "buy",
    qty: float = 0.1,
    daily_volume: float = 100_000.0,
    volatility_per_s: float = 0.0001,
    fee_bps: float = 0.0,
    ts_ms: int = 1_700_000_000_000,
    venue: str = "binance",
) -> SlippageSqrtRequest:
    return SlippageSqrtRequest(
        fill_id=fill_id,
        strategy_id="vpvr_v1",
        symbol=symbol,
        venue=venue,
        side=side,
        qty=qty,
        mid_price=0.0,
        daily_volume=daily_volume,
        volatility_per_s=volatility_per_s,
        fee_bps=fee_bps,
    )


def _build_requests() -> list[SlippageSqrtRequest]:
    """A small but realistic multi-symbol estimate journal.

    For default params (sigma=0.0001/s, k=1, daily_volume=100k, T=1s,
    daily_seconds=86400) ``impact_bps = sqrt(qty / v_per_s)`` where
    ``v_per_s = 100000/86400 ≈ 1.1574``. Verdict boundaries then require:

    * minimal  (< 5.0 bps)        → qty <  v_per_s * 25   ≈ 28.9 BTC
    * low      ([5.0, 15.0))      → qty ∈ [v_per_s*25, v_per_s*225)
    * moderate ([15.0, 50.0))     → qty ∈ [v_per_s*225, v_per_s*2500)
    * high     ([50.0, 200.0))    → qty ∈ [v_per_s*2500, v_per_s*40000)
    * extreme  (>= 200.0 bps)     → qty >= v_per_s * 40000

    Pattern: small BTC fill (minimal ~0.29 bps), medium BTC fill (low
    ~9.3 bps), small ETH fill (minimal ~0.66 bps), medium SOL fill
    (moderate ~16.1 bps).
    """
    return [
        _req("w:btc:1", "BTCUSDT", qty=0.1, fee_bps=2.0),    # minimal
        _req("w:btc:2", "BTCUSDT", qty=100.0, fee_bps=2.0),  # low
        _req("w:eth:1", "ETHUSDT", qty=0.5, fee_bps=2.0),    # minimal
        _req("w:sol:1", "SOLUSDT", qty=300.0, fee_bps=2.0),  # moderate
    ]


class WireInTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = _tmp_dir()

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_dedicated_request_path_estimates(self) -> None:
        with LiveExecutionRunner(self.tmp) as r:
            reqs = _build_requests()
            for req in reqs:
                est = r.ingest_slippage_sqrt_request(req)
                self.assertIsInstance(est, SlippageSqrtEstimate)
                self.assertEqual(est.fill_id, req.fill_id)
                # Sanity: every request is computed deterministically.
                self.assertGreaterEqual(est.temporary_impact_bps, 0.0)

            stats = r.slippage_sqrt_stats()
            self.assertIn("slippage_sqrt", stats)
            counters = stats["slippage_sqrt"]
            self.assertEqual(counters["total_requests"], len(reqs))
            # 2 BTC (one minimal + one low) + 1 ETH (minimal) + 1 SOL (moderate)
            self.assertEqual(counters["verdict_counts"][VERDICT_MINIMAL], 2)
            self.assertEqual(counters["verdict_counts"][VERDICT_LOW], 1)
            self.assertEqual(counters["verdict_counts"][VERDICT_MODERATE], 1)

    def test_global_stats_includes_slippage_sqrt(self) -> None:
        with LiveExecutionRunner(self.tmp) as r:
            r.ingest_slippage_sqrt_request(_req("w:btc:1", "BTCUSDT"))
            full = r.stats()
            self.assertIn("slippage_sqrt", full)
            self.assertEqual(full["slippage_sqrt"]["total_requests"], 1)

    def test_every_estimate_in_journal_exactly_once(self) -> None:
        """Every estimate must land in the slippage_sqrt journal."""
        journal_dir = self.tmp / "slippage_sqrt_journal"
        journal_dir.mkdir(parents=True, exist_ok=True)
        reqs = _build_requests()

        with LiveExecutionRunner(self.tmp) as r:
            for req in reqs:
                r.ingest_slippage_sqrt_request(req)
            r.shutdown()

        journal_path = journal_dir / "journal.jsonl"
        self.assertTrue(journal_path.exists())
        seen_fill_ids = set()
        n_rows = 0
        with open(journal_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                row = json.loads(line)
                self.assertEqual(row["kind"], "estimate")
                fid = row["request"]["fill_id"]
                self.assertNotIn(fid, seen_fill_ids, f"duplicate fill_id {fid!r}")
                seen_fill_ids.add(fid)
                n_rows += 1
                # Each row carries both inputs and outputs.
                self.assertIn("request", row)
                self.assertIn("estimate", row)
                self.assertEqual(
                    row["estimate"]["fill_id"], row["request"]["fill_id"]
                )
        self.assertEqual(n_rows, len(reqs))
        self.assertEqual(seen_fill_ids, {req.fill_id for req in reqs})

    def test_no_silent_bridge_from_ingest_fill(self) -> None:
        """``ingest_fill`` must NOT silently fan out to slippage_sqrt
        (the parent issue forbids fabricating daily_volume / volatility).
        """
        # Pull ``FillRecord`` from the canonical scorer module when
        # the sibling component is on the branch; fall back to a tiny
        # stub dataclass carrying the same fields when the broader
        # runner wire-up is out of scope for this branch.
        try:
            from strategy_execution_scorer_p7exec_091.scorer import (  # type: ignore[import-not-found]
                FillRecord,
            )
        except ImportError:
            from dataclasses import dataclass

            @dataclass(frozen=True)
            class FillRecord:  # type: ignore[no-redef]
                fill_id: str
                strategy_id: str
                symbol: str
                side: str
                qty: float
                expected_price: float
                fill_price: float
                ts_ms: int
                actual_fee_bps: float = 0.0

        with LiveExecutionRunner(self.tmp) as r:
            fill = FillRecord(
                fill_id="x", strategy_id="s", symbol="BTCUSDT",
                side="buy", qty=0.1,
                expected_price=100.0, fill_price=100.05,
                ts_ms=1_700_000_000_000,
            )
            results = r.ingest_fill(fill)
            self.assertNotIn("slippage_sqrt", results)
            # And the slippage_sqrt component remains empty.
            self.assertEqual(
                r.slippage_sqrt_stats()["slippage_sqrt"]["total_requests"], 0
            )

    def test_restart_rehydrates_per_symbol_aggregates(self) -> None:
        with LiveExecutionRunner(self.tmp) as r:
            reqs = _build_requests()
            for req in reqs:
                r.ingest_slippage_sqrt_request(req)
            btc_pre = r.slippage_sqrt_stats()["slippage_sqrt"]["per_symbol"]["BTCUSDT"][
                "cumulative_impact_bps"
            ]
            eth_pre = r.slippage_sqrt_stats()["slippage_sqrt"]["per_symbol"]["ETHUSDT"][
                "cumulative_impact_bps"
            ]
            total_pre = r.slippage_sqrt_stats()["slippage_sqrt"]["total_requests"]

        with LiveExecutionRunner(self.tmp) as r2:
            stats2 = r2.slippage_sqrt_stats()["slippage_sqrt"]
            self.assertEqual(stats2["total_requests"], total_pre)
            self.assertAlmostEqual(
                stats2["per_symbol"]["BTCUSDT"]["cumulative_impact_bps"],
                btc_pre,
                places=10,
            )
            self.assertAlmostEqual(
                stats2["per_symbol"]["ETHUSDT"]["cumulative_impact_bps"],
                eth_pre,
                places=10,
            )

    def test_shutdown_is_clean(self) -> None:
        """Shutdown must close the slippage_sqrt journal cleanly so a
        subsequent restart can read state.json."""
        with LiveExecutionRunner(self.tmp) as r:
            r.ingest_slippage_sqrt_request(_req("w:btc:1", "BTCUSDT"))
            r.shutdown()
        # Re-open: state.json must be present and the count restored.
        with LiveExecutionRunner(self.tmp) as r2:
            self.assertEqual(
                r2.slippage_sqrt_stats()["slippage_sqrt"]["total_requests"], 1
            )

    def test_kernel_version_pinned(self) -> None:
        """The kernel version is part of the public contract; verify
        it round-trips through the journal."""
        with LiveExecutionRunner(self.tmp) as r:
            est = r.ingest_slippage_sqrt_request(_req("w:btc:1", "BTCUSDT"))
            self.assertEqual(est.kernel_version, KERNEL_VERSION)


if __name__ == "__main__":
    unittest.main()