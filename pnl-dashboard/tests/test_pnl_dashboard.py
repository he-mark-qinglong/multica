"""Tests for :mod:`pnl_dashboard`.

Stdlib ``unittest`` only. Each test materialises a tiny synthetic
strategy tree under :func:`tempfile.mkdtemp` so the suite is hermetic.
"""
from __future__ import annotations

import csv
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

# Make the package importable when the suite is run from anywhere.
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import pnl_dashboard as pd  # noqa: E402


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _write_csv(path: Path, header: list[str], rows: list[list]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(header)
        for row in rows:
            writer.writerow(row)


def _write_summary(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload))


def _make_strategy(
    root: Path,
    name: str,
    trades: list[tuple],
    summary: dict | None = None,
) -> Path:
    """Create ``root/<name>/results/`` with one trades CSV and optional summary.

    Each ``trades`` tuple is
    ``(exit_ts, pnl_pct, [symbol], [direction], [reason])``; missing
    fields default to ``"BTCUSDT"``, ``"long"``, and ``"z_mean_revert"``.
    """
    strategy_dir = root / name / "results"
    header = [
        "symbol",
        "direction",
        "entry_ts",
        "exit_ts",
        "entry_price",
        "exit_price",
        "pnl_pct",
        "bars_held",
        "exit_reason",
    ]
    rows = []
    for t in trades:
        exit_ts, pnl = t[0], t[1]
        sym = t[2] if len(t) > 2 and t[2] is not None else "BTCUSDT"
        direction = t[3] if len(t) > 3 and t[3] is not None else "long"
        reason = t[4] if len(t) > 4 and t[4] is not None else "z_mean_revert"
        rows.append(
            [
                sym,
                direction,
                exit_ts,  # use exit_ts as entry_ts placeholder
                exit_ts,
                "100.0",
                "100.0",
                f"{pnl}",
                "1",
                reason,
            ]
        )
    _write_csv(strategy_dir / "trades_BTCUSDT.csv", header, rows)
    if summary is not None:
        _write_summary(strategy_dir / "summary.json", summary)
    return strategy_dir


# ---------------------------------------------------------------------------
# Parser / projection tests
# ---------------------------------------------------------------------------


class FirstExistingTests(unittest.TestCase):
    def test_returns_first_match(self) -> None:
        self.assertEqual(
            pd._first_existing(["x", "pnl_pct", "pnl"], pd.PNL_COL_CANDIDATES),
            "pnl_pct",
        )

    def test_returns_none_when_missing(self) -> None:
        self.assertIsNone(
            pd._first_existing(["x", "y"], pd.PNL_COL_CANDIDATES)
        )

    def test_handles_empty_header(self) -> None:
        self.assertIsNone(pd._first_existing([], pd.PNL_COL_CANDIDATES))


class CoerceFloatTests(unittest.TestCase):
    def test_int(self) -> None:
        self.assertEqual(pd._coerce_float(1), 1.0)

    def test_float(self) -> None:
        self.assertEqual(pd._coerce_float(0.5), 0.5)

    def test_string(self) -> None:
        self.assertEqual(pd._coerce_float("0.5"), 0.5)
        self.assertEqual(pd._coerce_float("-1.5"), -1.5)

    def test_nan_rejected(self) -> None:
        self.assertIsNone(pd._coerce_float(float("nan")))

    def test_empty_string_rejected(self) -> None:
        self.assertIsNone(pd._coerce_float(""))
        self.assertIsNone(pd._coerce_float("   "))

    def test_garbage_rejected(self) -> None:
        self.assertIsNone(pd._coerce_float("n/a"))

    def test_none_rejected(self) -> None:
        self.assertIsNone(pd._coerce_float(None))


class ProfitFactorTests(unittest.TestCase):
    def test_no_trades(self) -> None:
        self.assertEqual(pd._profit_factor([]), 0.0)

    def test_all_wins(self) -> None:
        self.assertEqual(pd._profit_factor([0.1, 0.2, 0.3]), 1.0)

    def test_balanced(self) -> None:
        # gross_win=0.6, gross_loss=0.3 => pf = 2.0
        self.assertAlmostEqual(pd._profit_factor([0.1, 0.2, -0.3]), 1.0)

    def test_heavy_loss(self) -> None:
        # gross_win=0.3, gross_loss=0.6 => pf = 0.5
        self.assertAlmostEqual(pd._profit_factor([0.1, 0.2, -0.3, -0.3]), 0.5)


class BucketMetricsTests(unittest.TestCase):
    def test_empty(self) -> None:
        m = pd._bucket_metrics([])
        self.assertEqual(m["n_trades"], 0)
        self.assertEqual(m["sum_pnl_pct"], 0.0)
        self.assertEqual(m["win_rate"], 0.0)

    def test_simple(self) -> None:
        m = pd._bucket_metrics([0.1, -0.2, 0.05])
        self.assertEqual(m["n_trades"], 3)
        self.assertAlmostEqual(m["sum_pnl_pct"], -0.05, places=6)
        self.assertAlmostEqual(m["win_rate"], 2 / 3, places=6)
        self.assertEqual(m["best_pnl_pct"], 0.1)
        self.assertEqual(m["worst_pnl_pct"], -0.2)


class ExitDayTests(unittest.TestCase):
    def test_iso_datetime(self) -> None:
        self.assertEqual(pd._exit_day("2024-09-06T10:30:00+00:00"), "2024-09-06")

    def test_date_only(self) -> None:
        self.assertEqual(pd._exit_day("2024-09-06"), "2024-09-06")

    def test_blank(self) -> None:
        self.assertIsNone(pd._exit_day(None))
        self.assertIsNone(pd._exit_day(""))
        self.assertIsNone(pd._exit_day("   "))

    def test_garbage(self) -> None:
        self.assertIsNone(pd._exit_day("n/a"))


class DownsampleTests(unittest.TestCase):
    def test_no_downsampling_when_short(self) -> None:
        pts = [{"ts": i, "cum_pnl_pct": i} for i in range(50)]
        out, downsampled = pd._downsample_curve(pts, 100)
        self.assertEqual(len(out), 50)
        self.assertFalse(downsampled)

    def test_downsamples_and_keeps_last(self) -> None:
        pts = [{"ts": i, "cum_pnl_pct": i} for i in range(1000)]
        out, downsampled = pd._downsample_curve(pts, 50)
        self.assertLessEqual(len(out), 51)
        self.assertTrue(downsampled)
        self.assertEqual(out[-1], pts[-1])


# ---------------------------------------------------------------------------
# CSV parsing
# ---------------------------------------------------------------------------


class ParseTradesCsvTests(unittest.TestCase):
    def test_happy_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            csv_path = Path(tmp) / "trades_BTCUSDT.csv"
            _write_csv(
                csv_path,
                ["symbol", "direction", "exit_ts", "pnl_pct"],
                [
                    ["BTCUSDT", "long", "2024-09-06T10:30:00+00:00", "0.10"],
                    ["ETHUSDT", "short", "2024-09-06T11:00:00+00:00", "-0.05"],
                ],
            )
            records, skipped = pd.parse_trades_csv("demo", csv_path)
            self.assertEqual(skipped, 0)
            self.assertEqual(len(records), 2)
            self.assertAlmostEqual(records[0].pnl, 0.10)
            self.assertEqual(records[0].symbol, "BTCUSDT")
            self.assertEqual(records[1].direction, "short")
            self.assertEqual(records[0].exit_ts, "2024-09-06T10:30:00+00:00")

    def test_falls_back_to_pnl_then_ret_pct(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            csv_path = Path(tmp) / "trades.csv"
            _write_csv(
                csv_path,
                ["symbol", "exit_ts", "ret_pct"],
                [["BTCUSDT", "2024-09-06", "0.02"]],
            )
            records, skipped = pd.parse_trades_csv("demo", csv_path)
            self.assertEqual(skipped, 0)
            self.assertEqual(len(records), 1)
            self.assertAlmostEqual(records[0].pnl, 0.02)

    def test_skips_malformed_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            csv_path = Path(tmp) / "trades.csv"
            _write_csv(
                csv_path,
                ["symbol", "exit_ts", "pnl_pct"],
                [
                    ["BTCUSDT", "2024-09-06", "0.10"],
                    ["BTCUSDT", "2024-09-06", "not_a_number"],
                    ["BTCUSDT", "2024-09-06", ""],
                    ["BTCUSDT", "2024-09-06", "0.05"],
                ],
            )
            records, skipped = pd.parse_trades_csv("demo", csv_path)
            self.assertEqual(len(records), 2)
            self.assertEqual(skipped, 2)
            self.assertAlmostEqual(records[0].pnl, 0.10)

    def test_missing_pnl_column_counts_all_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            csv_path = Path(tmp) / "trades.csv"
            _write_csv(
                csv_path,
                ["symbol", "exit_ts", "bogus"],
                [["BTCUSDT", "2024-09-06", "x"]] * 3,
            )
            records, skipped = pd.parse_trades_csv("demo", csv_path)
            self.assertEqual(records, [])
            self.assertEqual(skipped, 3)

    def test_empty_csv_returns_zero(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            csv_path = Path(tmp) / "trades.csv"
            _write_csv(csv_path, ["symbol", "exit_ts", "pnl_pct"], [])
            records, skipped = pd.parse_trades_csv("demo", csv_path)
            self.assertEqual(records, [])
            self.assertEqual(skipped, 0)

    def test_unreadable_file(self) -> None:
        # Point at a directory that doesn't exist as a file.
        records, skipped = pd.parse_trades_csv("demo", Path("/nonexistent/x.csv"))
        self.assertEqual(records, [])
        self.assertEqual(skipped, 0)


# ---------------------------------------------------------------------------
# summary.json projection
# ---------------------------------------------------------------------------


class ParseSummaryJsonTests(unittest.TestCase):
    def test_missing_file_returns_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = pd.parse_summary_json(Path(tmp))
            self.assertEqual(out, {})

    def test_projects_full_block(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            results = Path(tmp)
            _write_summary(
                results / "summary.json",
                {
                    "iteration": 107,
                    "tag": "PROFITABLE",
                    "campaign": "demo",
                    "hypothesis": "H3",
                    "portfolio_sharpe_daily_resampled": 1.5,
                    "portfolio_annualized_return_daily": 0.4,
                    "profit_factor_avg": 1.8,
                    "avg_pair_max_drawdown_pct": -0.15,
                    "n_trades_total": 12345,
                    "per_pair": {
                        "BTCUSDT/ETHUSDT": {
                            "n_trades": 12345,
                            "win_rate": 0.42,
                        }
                    },
                },
            )
            out = pd.parse_summary_json(results)
            self.assertEqual(out["tag"], "PROFITABLE")
            self.assertEqual(out["sharpe"], 1.5)
            self.assertEqual(out["annualized_return"], 0.4)
            self.assertEqual(out["profit_factor"], 1.8)
            self.assertEqual(out["max_drawdown_pct"], -0.15)
            self.assertEqual(out["n_trades"], 12345)
            self.assertAlmostEqual(out["win_rate"], 0.42)
            self.assertEqual(out["iteration"], 107)
            self.assertEqual(out["hypothesis"], "H3")

    def test_handles_garbage_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            results = Path(tmp)
            (results / "summary.json").write_text("not json {")
            self.assertEqual(pd.parse_summary_json(results), {})


# ---------------------------------------------------------------------------
# Filesystem walk
# ---------------------------------------------------------------------------


class IterStrategyDirsTests(unittest.TestCase):
    def test_skips_dirs_without_results(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "with_data" / "results").mkdir(parents=True)
            (root / "with_data" / "results" / "summary.json").write_text("{}")
            (root / "scaffold" / "tests").mkdir(parents=True)
            out = list(pd.iter_strategy_dirs(root))
            self.assertEqual([n for n, _ in out], ["with_data"])

    def test_missing_root_returns_nothing(self) -> None:
        self.assertEqual(list(pd.iter_strategy_dirs("/nonexistent/x")), [])


# ---------------------------------------------------------------------------
# End-to-end snapshot
# ---------------------------------------------------------------------------


class BuildSnapshotTests(unittest.TestCase):
    def _seed(self, tmp: str) -> Path:
        root = Path(tmp)
        # Strategy A: 3 winning trades + 1 losing, profitable summary.
        _make_strategy(
            root,
            "strat_a",
            [
                ("2024-09-06T10:00:00+00:00", 0.10, "BTCUSDT", "long"),
                ("2024-09-06T11:00:00+00:00", 0.20, "BTCUSDT", "long"),
                ("2024-09-06T12:00:00+00:00", 0.05, "ETHUSDT", "short"),
                ("2024-09-06T13:00:00+00:00", -0.15, "BTCUSDT", "long"),
            ],
            summary={
                "iteration": 10,
                "tag": "PROFITABLE",
                "portfolio_sharpe_daily_resampled": 1.8,
                "profit_factor_avg": 1.5,
                "avg_pair_max_drawdown_pct": -0.10,
                "portfolio_annualized_return_daily": 0.30,
                "n_trades_total": 4,
            },
        )
        # Strategy B: only losers, UNPROFITABLE summary.
        _make_strategy(
            root,
            "strat_b",
            [
                ("2024-09-07T10:00:00+00:00", -0.05, "ETHUSDT", "long"),
                ("2024-09-07T11:00:00+00:00", -0.03, "ETHUSDT", "long"),
            ],
            summary={
                "iteration": 20,
                "tag": "UNPROFITABLE",
                "portfolio_sharpe_daily_resampled": -0.5,
                "profit_factor_avg": 0.4,
                "avg_pair_max_drawdown_pct": -0.20,
                "n_trades_total": 2,
            },
        )
        # Strategy C: zero trades (only summary) — should NOT bump trades.
        _make_strategy(
            root,
            "strat_c_empty",
            [],
            summary={"iteration": 30, "tag": "REVIEW", "n_trades_total": 0},
        )
        return root

    def test_totals(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            snap = pd.build_snapshot(self._seed(tmp))
            self.assertEqual(snap.n_strategies_scanned, 3)
            self.assertEqual(snap.n_trades, 6)
            self.assertEqual(snap.n_strategies_with_trades, 2)
            # gross_win = 0.10 + 0.20 + 0.05 = 0.35; gross_loss = 0.15 + 0.05 + 0.03 = 0.23
            # pf = 0.35 / 0.23 ≈ 1.5217
            self.assertAlmostEqual(snap.sum_pnl_pct, 0.12, places=6)
            self.assertAlmostEqual(snap.profit_factor, 0.35 / 0.23, places=4)
            self.assertAlmostEqual(snap.best_trade_pnl_pct, 0.20)
            self.assertAlmostEqual(snap.worst_trade_pnl_pct, -0.15)
            self.assertEqual(snap.win_rate, 3 / 6)
            self.assertEqual(snap.unique_days, 2)
            self.assertEqual(snap.span_start, "2024-09-06")
            self.assertEqual(snap.span_end, "2024-09-07")
            self.assertEqual(snap.n_strategies_profitable, 1)

    def test_by_strategy_sorted_desc(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            snap = pd.build_snapshot(self._seed(tmp))
            names = [r["strategy"] for r in snap.by_strategy]
            self.assertEqual(names[0], "strat_a")
            self.assertAlmostEqual(snap.by_strategy[0]["sum_pnl_pct"], 0.20)
            self.assertEqual(snap.by_strategy[0]["summary_sharpe"], 1.8)
            self.assertEqual(snap.by_strategy[0]["summary_tag"], "PROFITABLE")
            # strat_b is the only losing strategy.
            self.assertEqual(names[-1], "strat_b")
            self.assertEqual(snap.by_strategy[-1]["summary_tag"], "UNPROFITABLE")

    def test_by_symbol_aggregation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            snap = pd.build_snapshot(self._seed(tmp))
            by_sym = {r["symbol"]: r for r in snap.by_symbol}
            self.assertEqual(by_sym["BTCUSDT"]["n_trades"], 3)
            # BTCUSDT pnls = [+0.10, +0.20, -0.15] -> sum = 0.15
            self.assertAlmostEqual(by_sym["BTCUSDT"]["sum_pnl_pct"], 0.15, places=6)
            self.assertEqual(by_sym["ETHUSDT"]["n_trades"], 3)
            # ETHUSDT pnls = [+0.05, -0.05, -0.03] -> sum = -0.03
            self.assertAlmostEqual(by_sym["ETHUSDT"]["sum_pnl_pct"], -0.03, places=6)

    def test_by_day_aggregation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            snap = pd.build_snapshot(self._seed(tmp))
            days = {r["date"]: r for r in snap.by_day}
            self.assertEqual(days["2024-09-06"]["n_trades"], 4)
            self.assertAlmostEqual(
                days["2024-09-06"]["sum_pnl_pct"], 0.10 + 0.20 + 0.05 - 0.15, places=6
            )
            self.assertEqual(days["2024-09-07"]["n_trades"], 2)

    def test_top_winners_and_losers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            snap = pd.build_snapshot(self._seed(tmp))
            # 6 trades total, top_n=10 (default) so all rows come back, sorted.
            self.assertEqual(len(snap.top_winners), 6)
            self.assertAlmostEqual(snap.top_winners[0]["pnl"], 0.20)
            self.assertEqual(snap.top_losers[0]["pnl"], -0.15)

    def test_equity_curve_monotonic_cum(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            snap = pd.build_snapshot(self._seed(tmp))
            self.assertEqual(snap.equity_curve_trade_count, 6)
            # Trades sorted by ts: [+0.10, +0.20, +0.05, -0.15, -0.05, -0.03]
            cum = []
            running = 0.0
            for pt in snap.equity_curve:
                running = pt["cum_pnl_pct"]
                cum.append(running)
            # Cumulative should hit 0.35, 0.55, 0.60, 0.45, 0.40, 0.37 in ts order.
            self.assertAlmostEqual(cum[0], 0.10, places=6)
            self.assertAlmostEqual(cum[-1], 0.12, places=6)
            # Drawdown max is peak - min after peak: peak=0.60, trough=0.37 -> 0.23
            self.assertAlmostEqual(snap.drawdown_max_pct, 0.23, places=6)

    def test_equity_curve_downsamples_when_long(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # 120 trades for one strategy.
            rows = []
            for i in range(120):
                rows.append(
                    (
                        f"2024-09-{(i % 28) + 1:02d}T{(i % 24):02d}:00:00+00:00",
                        0.001 * ((-1) ** i),
                        "BTCUSDT",
                        "long",
                    )
                )
            _make_strategy(root, "long_strat", rows)
            snap = pd.build_snapshot(root, equity_max_points=20)
            self.assertEqual(snap.equity_curve_trade_count, 120)
            self.assertTrue(snap.equity_curve_downsampled)
            self.assertLessEqual(len(snap.equity_curve), 21)
            # Last point's ts must equal the chronologically-last trade's ts
            # (the equity curve is sorted by ts before downsampling).
            chronological_last = max(rows, key=lambda r: r[0])[0]
            self.assertEqual(snap.equity_curve[-1]["ts"], chronological_last)

    def test_missing_root_does_not_blow_up(self) -> None:
        snap = pd.build_snapshot("/nonexistent/strategies_root")
        self.assertEqual(snap.n_strategies_scanned, 0)
        self.assertEqual(snap.n_trades, 0)
        self.assertEqual(snap.sum_pnl_pct, 0.0)
        self.assertEqual(snap.by_strategy, [])
        self.assertEqual(snap.equity_curve, [])


class WriteSnapshotTests(unittest.TestCase):
    def test_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_strategy(
                root,
                "demo",
                [("2024-09-06T10:00:00+00:00", 0.05, "BTCUSDT", "long")],
                summary={
                    "iteration": 1,
                    "tag": "PROFITABLE",
                    "n_trades_total": 1,
                    "portfolio_sharpe_daily_resampled": 0.5,
                },
            )
            snap = pd.build_snapshot(root)
            out_path = pd.write_snapshot(snap, Path(tmp) / "out" / "snapshot.json")
            self.assertTrue(out_path.is_file())
            loaded = json.loads(out_path.read_text())
            self.assertEqual(loaded["totals"]["n_trades"], 1)
            self.assertEqual(loaded["totals"]["n_strategies_scanned"], 1)
            self.assertIn("equity_curve", loaded)
            self.assertIn("by_strategy", loaded)
            self.assertIn("missing_sources", loaded)


if __name__ == "__main__":
    unittest.main()