"""Strict out-of-sample validation of the cross-venue funding-differential factor.

Follow-up to ``research/xs_funding/REPORT.md``, which found that extreme
binance-hyperliquid funding differentials are followed by price moves
*along* the high-funding side (momentum, not reversal) — but flagged three
caveats: (1) overlapping 24h/72h windows inflate naive t-stats (no
Newey-West), (2) 42 cells of multiple testing, (3) the direction flip was
chosen after seeing the results.

This script addresses all three with a strict protocol:

- **Train 2022-01-01 -> 2024-12-31**: ALL decisions (venue pair, threshold
  quantile, horizon, direction convention) are selected on train data only.
- **Test 2025-01-01 -> 2026-07-24** (price data end): the frozen config is
  applied verbatim. No re-tuning.
- **Newey-West t-stats** with lag = 2 * horizon_bars to correct for
  overlapping forward windows, reported next to the naive iid t.
- **Multiple testing**: Bonferroni over the 42-cell family plus a Deflated
  Sharpe Ratio view (Bailey & Lopez de Prado 2014).
- **Non-overlapping execution variant**: after an event fires, no new
  position for ``horizon`` hours — the tradeable number.

The expanding-quantile event threshold at time t only ever sees |diff|
values strictly before t (``shift(1)``); the train/test split governs
*parameter selection*, not the information set — at test time the threshold
legitimately uses all past funding history.

Usage::

    python3 scripts/xs_funding_oos_validation.py            # full run + md report
    python3 scripts/xs_funding_oos_validation.py --no-report
"""
from __future__ import annotations

import argparse
import math
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, "/Users/mark/multica/quant-loop")
from scripts.xs_funding_factor import (  # noqa: E402
    SYMBOLS,
    PAIRS,
    GRID_H,
    load_funding_8h,
    load_price_close_8h,
    funding_diff,
    detect_events,
)

ROOT = Path("/Users/mark/multica/quant-loop")
REPORT_PATH = ROOT / "research" / "xs_funding" / "OOS_VALIDATION.md"

TRAIN_END = pd.Timestamp("2025-01-01", tz="UTC")

QUANTILES = [0.85, 0.90, 0.95]
HORIZONS_H = [8, 24, 72]
DIRECTIONS = {"reversal": -1.0, "momentum": +1.0}  # multiplier on sign(diff)
# Full search family actually evaluated on train (used for DSR n_trials):
# 2 pairs x 3 quantiles x 3 horizons x 2 directions = 36. The original
# REPORT.md family was 42 cells (7 symbols x 2 pairs x 3 horizons); we
# report Bonferroni against both 36 (config search) and 42 (cell family).
N_TRIALS_SEARCH = len(PAIRS) * len(QUANTILES) * len(HORIZONS_H) * len(DIRECTIONS)
N_CELLS_FAMILY = 42
MIN_TEST_EVENTS = 30


@dataclass(frozen=True)
class Config:
    """Frozen strategy configuration selected on train data."""

    pair: tuple[str, str]
    quantile: float
    horizon_h: int
    direction_mult: float  # +1 momentum (along high-funding side), -1 reversal

    @property
    def pair_name(self) -> str:
        return f"{self.pair[0]}-{self.pair[1]}"

    @property
    def horizon_bars(self) -> int:
        return self.horizon_h // GRID_H


@dataclass(frozen=True)
class SegmentStats:
    """Event-study statistics for one (config, segment, symbol-scope)."""

    segment: str  # "train" | "test"
    scope: str  # symbol or "POOLED"
    variant: str  # "overlap" | "nonoverlap"
    n_events: int
    mean_ret: float
    t_naive: float
    t_nw: float
    win_rate: float


def newey_west_t(returns: np.ndarray, lag: int) -> float:
    """Newey-West (HAC) t-stat of the mean of a (possibly autocorrelated) series.

    Bartlett kernel, bandwidth ``lag``. For overlapping h-bar forward
    returns the first h-1 autocovariances are mechanically non-zero; the
    protocol uses lag = 2 * horizon_bars to also catch event clustering.
    """
    x = np.asarray(returns, dtype=float)
    n = len(x)
    if n < 2:
        return math.nan
    xc = x - x.mean()
    gamma0 = float(xc @ xc) / n
    var_mean = gamma0 / n
    for k in range(1, min(lag, n - 1) + 1):
        gk = float(xc[k:] @ xc[:-k]) / n
        var_mean += 2.0 * (1.0 - k / (lag + 1.0)) * gk / n
    if var_mean <= 0:
        return math.nan
    return float(x.mean() / math.sqrt(var_mean))


def naive_t(returns: np.ndarray) -> float:
    """Plain iid t-stat of the mean (biased upward under overlap; kept for contrast)."""
    x = np.asarray(returns, dtype=float)
    n = len(x)
    if n < 2:
        return math.nan
    s = float(x.std(ddof=1))
    if s <= 0:
        return math.nan
    return float(x.mean() / (s / math.sqrt(n)))


def signed_forward_returns(
    events: pd.Series, close_8h: pd.Series, horizon_h: int, direction_mult: float
) -> pd.Series:
    """Signal-signed forward returns; direction = direction_mult * sign(diff).

    Same alignment discipline as the original study: positional division on
    the ffilled price grid, events without t+h price coverage dropped.
    """
    px = close_8h.reindex(close_8h.index.union(events.index)).ffill()
    p0 = px.reindex(events.index)
    p1 = px.reindex(events.index + pd.Timedelta(hours=horizon_h))
    fwd = pd.Series(p1.to_numpy() / p0.to_numpy() - 1.0, index=events.index).dropna()
    direction = direction_mult * np.sign(events.reindex(fwd.index))
    return (direction * fwd).dropna()


def filter_non_overlapping(events: pd.Series, horizon_h: int) -> pd.Series:
    """Keep only events at least ``horizon_h`` after the previously accepted one.

    Mirrors live execution: one position per symbol at a time, held for the
    full horizon; signals arriving while a position is open are ignored.
    """
    keep: list[pd.Timestamp] = []
    last: pd.Timestamp | None = None
    gap = pd.Timedelta(hours=horizon_h)
    for ts in events.index:
        if last is None or ts - last >= gap:
            keep.append(ts)
            last = ts
    return events.loc[keep]


def segment_stats(
    segment: str, scope: str, variant: str, returns: np.ndarray, lag: int
) -> SegmentStats:
    x = np.asarray(returns, dtype=float)
    n = len(x)
    return SegmentStats(
        segment=segment,
        scope=scope,
        variant=variant,
        n_events=n,
        mean_ret=float(x.mean()) if n else math.nan,
        t_naive=naive_t(x),
        t_nw=newey_west_t(x, lag),
        win_rate=float((x > 0).mean()) if n else math.nan,
    )


def build_event_returns(
    cfg: Config,
    diffs: dict[str, pd.Series],
    closes: dict[str, pd.Series],
    nonoverlap: bool = False,
) -> dict[str, pd.Series]:
    """Signed forward returns per symbol for a config (pure)."""
    out: dict[str, pd.Series] = {}
    for symbol in SYMBOLS:
        diff = diffs[symbol]
        events = detect_events(diff, quantile=cfg.quantile)
        if nonoverlap:
            events = filter_non_overlapping(events, cfg.horizon_h)
        out[symbol] = signed_forward_returns(
            events, closes[symbol], cfg.horizon_h, cfg.direction_mult
        )
    return out


def _pool(rets: dict[str, pd.Series], start: pd.Timestamp, end: pd.Timestamp) -> np.ndarray:
    parts = [r[(r.index >= start) & (r.index < end)].to_numpy() for r in rets.values()]
    parts = [p for p in parts if len(p)]
    return np.concatenate(parts) if parts else np.array([])


def select_config(
    diffs: dict[tuple[str, str], dict[str, pd.Series]],
    closes: dict[str, pd.Series],
) -> tuple[Config, list[dict]]:
    """Grid-search the full family on TRAIN only; return the frozen winner.

    Scoring: pooled train Newey-West t (lag = 2*horizon_bars). Direction is
    part of the search (both conventions tried), which is honestly counted
    in N_TRIALS_SEARCH for the Deflated Sharpe view.
    """
    train_end = TRAIN_END
    leaderboard: list[dict] = []
    best: Config | None = None
    best_t = -math.inf
    for pair in PAIRS:
        pair_diffs = diffs[pair]
        for q in QUANTILES:
            for h in HORIZONS_H:
                for dname, dmult in DIRECTIONS.items():
                    cfg = Config(pair=pair, quantile=q, horizon_h=h, direction_mult=dmult)
                    rets = build_event_returns(cfg, pair_diffs, closes)
                    pooled = _pool(rets, pd.Timestamp.min.tz_localize("UTC"), train_end)
                    if len(pooled) < 20:
                        continue
                    t_nw = newey_west_t(pooled, lag=2 * cfg.horizon_bars)
                    t_nv = naive_t(pooled)
                    leaderboard.append(
                        {
                            "pair": cfg.pair_name, "q": q, "h": h, "dir": dname,
                            "n_train": len(pooled), "mean": float(pooled.mean()),
                            "t_naive": t_nv, "t_nw": t_nw,
                        }
                    )
                    if not math.isnan(t_nw) and t_nw > best_t:
                        best_t, best = t_nw, cfg
    if best is None:
        raise RuntimeError("no config had enough train events")
    leaderboard.sort(key=lambda r: -r["t_nw"])
    return best, leaderboard


def evaluate_segment(
    cfg: Config,
    diffs: dict[str, pd.Series],
    closes: dict[str, pd.Series],
    start: pd.Timestamp,
    end: pd.Timestamp,
    segment: str,
) -> list[SegmentStats]:
    """Per-symbol + pooled stats for both variants over one segment."""
    lag = 2 * cfg.horizon_bars
    stats: list[SegmentStats] = []
    for variant, nonoverlap in (("overlap", False), ("nonoverlap", True)):
        rets = build_event_returns(cfg, diffs, closes, nonoverlap=nonoverlap)
        for symbol in SYMBOLS:
            r = rets[symbol]
            x = r[(r.index >= start) & (r.index < end)].to_numpy()
            stats.append(segment_stats(segment, symbol, variant, x, lag))
        stats.append(segment_stats(segment, "POOLED", variant, _pool(rets, start, end), lag))
    return stats


def bonferroni_alpha(n_tests: int, alpha: float = 0.05) -> float:
    """Two-sided normal critical value under Bonferroni."""
    from statistics import NormalDist

    return NormalDist().inv_cdf(1.0 - alpha / (2.0 * n_tests))


def deflated_sharpe_view(returns: np.ndarray, n_trials: int) -> dict:
    """DSR of the event-return stream, reusing the shared cpcv implementation.

    The shared ``deflated_sharpe`` returns observed SR minus the
    multiple-testing hurdle (expected max SR under the null); the edge is
    real at 95% iff that value is > 0.
    """
    sys.path.insert(0, str(ROOT / "_shared" / "validation"))
    from cpcv import deflated_sharpe

    x = np.asarray(returns, dtype=float)
    if len(x) < 3:
        return {"sr": math.nan, "dsr": math.nan, "skew": math.nan, "kurt": math.nan}
    sr = float(x.mean() / x.std(ddof=1))  # per-event Sharpe
    m = x - x.mean()
    skew = float((m**3).mean() / (m**2).mean() ** 1.5)
    kurt = float((m**4).mean() / (m**2).mean() ** 2)
    dsr = deflated_sharpe(sr, n_trials=n_trials, sample_len=len(x), skew=skew, kurt=kurt)
    return {"sr": sr, "dsr": dsr, "skew": skew, "kurt": kurt}


def _pct(x: float) -> str:
    return "n/a" if x is None or math.isnan(x) else f"{x * 100:.3f}%"


def _num(x: float) -> str:
    return "n/a" if x is None or math.isnan(x) else f"{x:.2f}"


def write_report(
    cfg: Config,
    leaderboard: list[dict],
    stats: list[SegmentStats],
    dsr_overlap: dict,
    dsr_nonoverlap: dict,
    verdict: str,
    verdict_reasons: list[str],
    path: Path = REPORT_PATH,
) -> None:
    lines = [
        "# XS funding 差因子 — 严格 OOS 验证",
        "",
        "生成脚本：`scripts/xs_funding_oos_validation.py`"
        "（测试见 `scripts/test_xs_funding_oos_validation.py`）",
        "",
        "## 协议",
        "",
        "- **Train 2022-01-01 → 2024-12-31**：全部决策（所对、阈值分位数、horizon、方向）"
        "只在 train 上选择。",
        "- **Test 2025-01-01 → 2026-07-24**（价格数据末端）：冻结配置原样应用，不调参。",
        "- 事件阈值仍为 expanding 分位数（shift(1)，热身 90 根 8h bar）；test 段阈值合法地"
        "使用全部历史（含 train）——划分约束的是参数选择，不是信息集。",
        "- Newey-West t：lag = 2 × horizon_bars，修正重叠前向窗口的自相关。",
        f"- 搜索族：{N_TRIALS_SEARCH} 个配置（2 所对 × 3 分位数 × 3 horizon × 2 方向），"
        f"另对原报告 {N_CELLS_FAMILY} 格子族做 Bonferroni。",
        "- 非重叠执行版：事件触发后 horizon 小时内不再开新仓（与实盘一致）。",
        "",
        "## Train 段选出的冻结配置",
        "",
        f"- 所对：**{cfg.pair_name}**，阈值分位数 **{cfg.quantile}**，"
        f"horizon **{cfg.horizon_h}h**，方向 **{'动量（顺着 funding 高的一方）' if cfg.direction_mult > 0 else '反转'}**",
        "",
        "### Train leaderboard（top 10，按 train NW-t 排序）",
        "",
        "| pair | q | horizon | 方向 | n_train | mean | naive t | NW t |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in leaderboard[:10]:
        lines.append(
            f"| {r['pair']} | {r['q']} | {r['h']}h | {r['dir']} | {r['n_train']} "
            f"| {_pct(r['mean'])} | {_num(r['t_naive'])} | {_num(r['t_nw'])} |"
        )
    lines += [
        "",
        "## 分段结果（冻结配置）",
        "",
        "| 段 | 变体 | 范围 | n | mean | naive t | NW t | 胜率 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for s in stats:
        lines.append(
            f"| {s.segment} | {s.variant} | {s.scope} | {s.n_events} | {_pct(s.mean_ret)} "
            f"| {_num(s.t_naive)} | {_num(s.t_nw)} | {_pct(s.win_rate)} |"
        )
    z36 = bonferroni_alpha(N_TRIALS_SEARCH)
    z42 = bonferroni_alpha(N_CELLS_FAMILY)
    lines += [
        "",
        "## 多重检验校正",
        "",
        f"- Bonferroni 临界 |t|（双侧 α=0.05）：搜索族 {N_TRIALS_SEARCH} → **{z36:.2f}**；"
        f"原报告格子族 {N_CELLS_FAMILY} → **{z42:.2f}**。",
        f"- Deflated Sharpe（重叠版 test 事件流，n_trials={N_TRIALS_SEARCH}）："
        f"per-event SR={_num(dsr_overlap['sr'])}，DSR 值（observed − 多重检验障碍）"
        f"={_num(dsr_overlap['dsr'])}（skew={_num(dsr_overlap['skew'])}，kurt={_num(dsr_overlap['kurt'])}）。",
        f"- Deflated Sharpe（非重叠版 test 事件流，n_trials={N_TRIALS_SEARCH}）："
        f"per-event SR={_num(dsr_nonoverlap['sr'])}，DSR 值={_num(dsr_nonoverlap['dsr'])}。",
        "- 判定口径（与 `_shared/validation/cpcv.py` 一致）：DSR 值 > 0 ⇒ 经多重检验校正后仍显著。",
        "",
        "## 判定",
        "",
        f"**{verdict}**",
        "",
    ]
    lines += [f"- {r}" for r in verdict_reasons]
    lines += [
        "",
        "## 残余警示（如实记录）",
        "",
        "- binance-HL 所对的 train 段只有 2023-05 → 2024-12（HL 数据起点），约 19 个月。",
        "- 未计手续费与资金费收付本身；纯价格收益视角。执行价用 8h 网格 close，"
        "实盘滑点未建模。",
        "- 跨标的 pooled 统计把同时段不同标的的事件当作独立观测，相关性会抬高 pooled t；"
        "分标的 NW-t 更可信。",
        "- 方向虽由 train 段独立选出，但本研究整体仍受原报告启发（研究者自由度无法完全消除）。",
        "- expanding 分位数阈值只升不降（历史极端值永久抬升阈值），导致 test 段部分标的事件稀少"
        "（BTC 仅 7 个、DOGE 仅 7 个）——这本身就是该因子定义的一个结构性缺陷。",
        "- train  leaderboard 顶部配置 NW-t 超过 Bonferroni 临界属预期：那是**选择后**的最大值，"
        "不代表任何单一配置的先验显著性；真正的检验只有 test 段，而 test 段失败。",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--no-report", action="store_true")
    args = parser.parse_args(argv)

    closes = {s: load_price_close_8h(s) for s in SYMBOLS}
    venues = sorted({v for pair in PAIRS for v in pair})
    funding = {(v, s): load_funding_8h(v, s) for v in venues for s in SYMBOLS}
    diffs: dict[tuple[str, str], dict[str, pd.Series]] = {
        pair: {s: funding_diff(funding[(pair[0], s)], funding[(pair[1], s)]) for s in SYMBOLS}
        for pair in PAIRS
    }

    cfg, leaderboard = select_config(diffs, closes)
    print(
        f"frozen config: pair={cfg.pair_name} q={cfg.quantile} "
        f"horizon={cfg.horizon_h}h direction_mult={cfg.direction_mult:+.0f}"
    )

    price_end = max(c.index.max() for c in closes.values())
    stats = []
    stats += evaluate_segment(
        cfg, diffs[cfg.pair], closes,
        pd.Timestamp.min.tz_localize("UTC"), TRAIN_END, "train",
    )
    stats += evaluate_segment(
        cfg, diffs[cfg.pair], closes, TRAIN_END, price_end + pd.Timedelta(hours=GRID_H), "test",
    )

    test_pooled = next(s for s in stats if s.segment == "test" and s.scope == "POOLED" and s.variant == "overlap")
    test_pooled_no = next(s for s in stats if s.segment == "test" and s.scope == "POOLED" and s.variant == "nonoverlap")
    train_pooled = next(s for s in stats if s.segment == "train" and s.scope == "POOLED" and s.variant == "overlap")

    rets_test_overlap = _pool(
        build_event_returns(cfg, diffs[cfg.pair], closes, nonoverlap=False),
        TRAIN_END, price_end + pd.Timedelta(hours=GRID_H),
    )
    rets_test_nonoverlap = _pool(
        build_event_returns(cfg, diffs[cfg.pair], closes, nonoverlap=True),
        TRAIN_END, price_end + pd.Timedelta(hours=GRID_H),
    )
    dsr_overlap = deflated_sharpe_view(rets_test_overlap, N_TRIALS_SEARCH)
    dsr_nonoverlap = deflated_sharpe_view(rets_test_nonoverlap, N_TRIALS_SEARCH)

    z_crit = bonferroni_alpha(N_TRIALS_SEARCH)
    direction_consistent = (
        not math.isnan(test_pooled.mean_ret)
        and not math.isnan(train_pooled.mean_ret)
        and test_pooled.mean_ret > 0
    )
    passed = (
        not math.isnan(test_pooled.t_nw)
        and test_pooled.t_nw > 2.0
        and direction_consistent
        and test_pooled_no.n_events >= MIN_TEST_EVENTS
    )
    verdict = "PASS" if passed else "KILL"
    reasons = [
        f"test 重叠版 pooled：n={test_pooled.n_events}, mean={_pct(test_pooled.mean_ret)}, "
        f"naive t={_num(test_pooled.t_naive)}, NW t={_num(test_pooled.t_nw)}, "
        f"胜率={_pct(test_pooled.win_rate)}",
        f"test 非重叠执行版 pooled：n={test_pooled_no.n_events}, "
        f"mean={_pct(test_pooled_no.mean_ret)}, naive t={_num(test_pooled_no.t_naive)}, "
        f"NW t={_num(test_pooled_no.t_nw)}, 胜率={_pct(test_pooled_no.win_rate)}",
        f"NW t > 2 要求：{'满足' if not math.isnan(test_pooled.t_nw) and test_pooled.t_nw > 2 else '不满足'}；"
        f"方向一致性要求（test mean > 0 与 train 选出方向一致）：{'满足' if direction_consistent else '不满足'}；"
        f"非重叠事件数 ≥ {MIN_TEST_EVENTS}：{'满足' if test_pooled_no.n_events >= MIN_TEST_EVENTS else '不满足'}",
        f"Bonferroni 临界（搜索族 {N_TRIALS_SEARCH}）：|t| > {z_crit:.2f} — "
        f"{'通过' if not math.isnan(test_pooled.t_nw) and abs(test_pooled.t_nw) > z_crit else '未通过'}",
        f"DSR（非重叠版）：observed SR {_num(dsr_nonoverlap['sr'])}，DSR 值 "
        f"{_num(dsr_nonoverlap['dsr'])}（>0 才算校正后显著）— "
        f"{'通过' if not math.isnan(dsr_nonoverlap['dsr']) and dsr_nonoverlap['dsr'] > 0 else '未通过'}",
    ]
    for r in reasons:
        print(r)
    print(f"VERDICT: {verdict}")

    if not args.no_report:
        write_report(cfg, leaderboard, stats, dsr_overlap, dsr_nonoverlap, verdict, reasons)
        print(f"report written: {REPORT_PATH}")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
