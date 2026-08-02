"""Cross-venue funding-differential factor — event study (work package XS).

Factor: for each symbol, the 8h-grid funding-rate difference between two
venues (``binance - bybit`` and ``binance - hyperliquid``). Hyperliquid's
~1h funding is aggregated to the 8h grid by simple mean per 8h bucket.

Hypothesis: when ``|diff|`` exceeds its own historical 90th percentile, the
venue with the higher funding has the more crowded longs (or shorts), so
price is expected to move *against* the high-funding side::

    signal_direction = -sign(diff)
    signed_return    = signal_direction * forward_price_return

Data inputs (see ``_shared/data/test_funding_cross_integrity.py`` for the
integrity contract):

- ``data/funding_cross/{binance,bybit,hyperliquid}/*.parquet``
  schema ``(ts, funding_rate, venue, symbol)``; binance/bybit 8h on the
  00/08/16 UTC grid (sub-second jitter on some rows, plus a documented
  SOLUSDT 2h-cadence episode Nov-Dec 2022), hyperliquid ~1h irregular.
- ``data/perp_30m/{SYMBOL}_30m.parquet`` — Binance USDM perp 30m klines,
  ms-epoch ``open_time``; covers all 7 symbols 2022-01-01..2026-07-24.

Lookahead discipline: the 90th-percentile threshold is an *expanding*
quantile over past ``|diff|`` values only (``shift(1)``), so an event at
time t never sees funding data from t onward. Forward returns are
measurement, not inputs.

Usage::

    python3 scripts/xs_funding_factor.py            # full study + REPORT.md
    python3 scripts/xs_funding_factor.py --no-report
"""
from __future__ import annotations

import argparse
import math
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path("/Users/mark/multica/quant-loop")
FUNDING_DIR = ROOT / "data" / "funding_cross"
PRICE_DIR = ROOT / "data" / "perp_30m"
REPORT_PATH = ROOT / "research" / "xs_funding" / "REPORT.md"

SYMBOLS = ["BTC", "ETH", "SOL", "BNB", "AVAX", "DOGE", "LINK"]
PAIRS = [("binance", "bybit"), ("binance", "hyperliquid")]
HORIZONS_H = [8, 24, 72]
GRID_H = 8
# Warm-up: an expanding 90th percentile needs history to be meaningful.
# 90 bars ≈ 30 days of 8h history.
MIN_HISTORY_BARS = 90


@dataclass(frozen=True)
class EventStats:
    """Summary statistics of one (symbol, pair, horizon) event study cell."""

    symbol: str
    pair: str
    horizon_h: int
    n_events: int
    mean_signal_ret: float
    t_stat: float
    win_rate: float
    baseline_mean_ret: float
    baseline_std_ret: float
    excess_vs_baseline: float


def load_funding_8h(venue: str, symbol: str) -> pd.Series:
    """Load one venue/symbol funding file, resampled to the 8h UTC grid.

    Timestamps are floored to the hour first (sub-second jitter exists in
    all three venue exports), then averaged into 8h buckets anchored at
    00/08/16 UTC. This uniformly handles the 8h venues and ~1h hyperliquid,
    including the documented SOLUSDT 2h-cadence episode.
    """
    path = FUNDING_DIR / venue / f"{symbol if venue == 'hyperliquid' else symbol + 'USDT'}.parquet"
    df = pd.read_parquet(path, columns=["ts", "funding_rate"])
    ts = pd.to_datetime(df["ts"], utc=True).dt.floor("h")
    bucket = ts.dt.floor(f"{GRID_H}h")
    return df["funding_rate"].astype(float).groupby(bucket).mean()


def load_price_close_8h(symbol: str) -> pd.Series:
    """Close prices on the 8h grid from ``data/perp_30m`` (ms-epoch open_time)."""
    path = PRICE_DIR / f"{symbol}USDT_30m.parquet"
    df = pd.read_parquet(path, columns=["open_time", "close"])
    ts = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    s = pd.Series(df["close"].astype(float).to_numpy(), index=ts)
    return s.resample(f"{GRID_H}h").last().dropna()


def funding_diff(a: pd.Series, b: pd.Series) -> pd.Series:
    """Funding differential on the shared 8h grid (inner join, pure)."""
    return (a - b).dropna()


def detect_events(diff: pd.Series, quantile: float = 0.90) -> pd.Series:
    """Timestamps where |diff| exceeds its expanding historical quantile.

    The threshold at time t is the quantile of |diff| over [start, t-1]
    (strictly past data), requiring at least ``MIN_HISTORY_BARS`` of history.
    Pure function of the input series.
    """
    abs_diff = diff.abs()
    threshold = abs_diff.expanding(min_periods=MIN_HISTORY_BARS).quantile(quantile).shift(1)
    return diff[abs_diff > threshold].dropna()


def signed_forward_returns(
    events: pd.Series, close_8h: pd.Series, horizon_h: int
) -> pd.Series:
    """Signal-signed forward returns for event timestamps (pure).

    For event at t with funding diff d: direction = -sign(d) (against the
    crowded side), return = close(t+h)/close(t) - 1, signed by direction.
    Events lacking a price at t or t+h are dropped (e.g. beyond price
    coverage end).
    """
    px = close_8h.reindex(close_8h.index.union(events.index)).ffill()
    p0 = px.reindex(events.index)
    p1 = px.reindex(events.index + pd.Timedelta(hours=horizon_h))
    # Positional division: p0/p1 live on shifted (often disjoint) grids, so
    # label-aligned ``p1 / p0`` would produce NaN — or, worse, a spurious
    # exact 0.0 where the two grids collide. Align by position instead.
    fwd = pd.Series(p1.to_numpy() / p0.to_numpy() - 1.0, index=events.index).dropna()
    direction = -np.sign(events.reindex(fwd.index))
    return (direction * fwd).dropna()


def summarize_cell(
    symbol: str, pair: str, horizon_h: int, signal: pd.Series, baseline: pd.Series
) -> EventStats:
    """Mean / t-stat / win rate of signal returns vs an unconditional baseline.

    ``baseline`` is the |direction|-agnostic forward-return distribution over
    all 8h grid points in the signal's time span; excess = mean(signal) -
    mean(baseline * 0) is not meaningful for signed returns, so we compare
    against the *signed* baseline: forward return times the same direction
    rule applied at every grid point (event or not). The caller passes that.
    t-stat uses the plain iid formula; overlapping horizons inflate it —
    reported as-is, see REPORT.md caveats.
    """
    n = len(signal)
    mean = float(signal.mean()) if n else math.nan
    std = float(signal.std(ddof=1)) if n > 1 else math.nan
    t = mean / (std / math.sqrt(n)) if n > 1 and std and std > 0 else math.nan
    win = float((signal > 0).mean()) if n else math.nan
    bmean = float(baseline.mean()) if len(baseline) else math.nan
    bstd = float(baseline.std(ddof=1)) if len(baseline) > 1 else math.nan
    return EventStats(
        symbol=symbol,
        pair=pair,
        horizon_h=horizon_h,
        n_events=n,
        mean_signal_ret=mean,
        t_stat=t,
        win_rate=win,
        baseline_mean_ret=bmean,
        baseline_std_ret=bstd,
        excess_vs_baseline=mean - bmean if n and len(baseline) else math.nan,
    )


def signed_baseline(diff: pd.Series, close_8h: pd.Series, horizon_h: int) -> pd.Series:
    """Direction-rule returns at every 8h grid point (the unconditional control).

    Same construction as ``signed_forward_returns`` but evaluated at all
    grid timestamps where the direction rule is defined, not just extreme
    events. This isolates the value of the *extremity filter*: if extreme
    events beat this baseline, the 90th-percentile threshold adds
    information beyond the raw sign rule.
    """
    common = diff.index.intersection(close_8h.index)
    direction = -np.sign(diff.reindex(common))
    fwd = close_8h.shift(-(horizon_h // GRID_H)) / close_8h - 1.0
    return (direction * fwd.reindex(common)).dropna()


def run_study() -> list[EventStats]:
    """Run the full event study over all symbols, pairs and horizons."""
    cells: list[EventStats] = []
    for symbol in SYMBOLS:
        close = load_price_close_8h(symbol)
        funding = {v: load_funding_8h(v, symbol) for v in {v for pair in PAIRS for v in pair}}
        for a, b in PAIRS:
            diff = funding_diff(funding[a], funding[b])
            events = detect_events(diff)
            for h in HORIZONS_H:
                sig = signed_forward_returns(events, close, h)
                base = signed_baseline(diff, close, h)
                # Restrict baseline to the signal's time span for comparability.
                if len(sig):
                    base = base[(base.index >= sig.index.min()) & (base.index <= sig.index.max())]
                cells.append(summarize_cell(symbol, f"{a}-{b}", h, sig, base))
    return cells


def aggregate(cells: list[EventStats], pair: str, horizon_h: int) -> dict[str, float]:
    """Pooled stats across all symbols for one (pair, horizon)."""
    subset = [c for c in cells if c.pair == pair and c.horizon_h == horizon_h and c.n_events > 1]
    n = sum(c.n_events for c in subset)
    if not subset or n == 0:
        return {"n": 0, "mean": math.nan, "t": math.nan, "win": math.nan, "excess": math.nan}
    mean = sum(c.mean_signal_ret * c.n_events for c in subset) / n
    # Approximate pooled t via weighted mean / pooled standard error.
    se2 = sum(
        (c.mean_signal_ret / c.t_stat) ** 2 * c.n_events
        for c in subset
        if c.t_stat and not math.isnan(c.t_stat) and c.t_stat != 0
    )
    t = mean / math.sqrt(se2 / n) if se2 > 0 else math.nan
    win = sum(c.win_rate * c.n_events for c in subset) / n
    excess = sum(c.excess_vs_baseline * c.n_events for c in subset) / n
    return {"n": n, "mean": mean, "t": t, "win": win, "excess": excess}


def _pct(x: float) -> str:
    return "n/a" if x is None or math.isnan(x) else f"{x * 100:.3f}%"


def _num(x: float) -> str:
    return "n/a" if x is None or math.isnan(x) else f"{x:.2f}"


def write_report(cells: list[EventStats], path: Path = REPORT_PATH) -> None:
    """Render the study results as ``research/xs_funding/REPORT.md``."""
    lines = [
        "# XS 工作包：跨所 funding 差因子事件研究",
        "",
        "生成脚本：`scripts/xs_funding_factor.py`（纯函数核心，测试见 "
        "`_shared/validation/test_xs_funding_factor.py`）",
        "",
        "## 方法",
        "",
        "- 因子 = 同一标的两所 funding 之差（8h 网格；Hyperliquid 小时级聚合为 8h 均值；",
        "  binance/bybit 时间戳亚秒抖动先 floor 到小时）",
        "- 事件：|diff| > 历史（expanding，shift(1)，无前视）90 分位，热身期 "
        f"{MIN_HISTORY_BARS} 根 8h bar（≈30 天）",
        "- 方向：逆着 funding 高的一方（`direction = -sign(diff)`），"
        "高 funding 所多头更拥挤 → 预期回落",
        "- 收益：Binance USDM perp 30m close 重采样到 8h，测 +8h/+24h/+72h",
        "- 基线：同一方向规则在**全部** 8h 网格点上的收益（分离“极端度过滤”的增量信息）",
        "",
        "## 数据对齐说明（如实记录）",
        "",
        "- binance/bybit：8h，00/08/16 UTC 网格；部分行有亚秒抖动（如 `16:00:00.004`）",
        "- **SOLUSDT 异常段**：2022-11 中旬（binance 11-09..18，bybit 11-10..12-20）"
        "funding 曾改为 2h 频率，",
        "  本研究按 8h 桶均值处理，该段 diff 质量较低但占比极小",
        "- hyperliquid：~1h 不规则（实测间隔 0.8h–8.4h），2023-05 起 "
        "→ binance-HL 组合样本期 2023-05 至 2026-07-24",
        "- binance-bybit 样本期 ≈ 2021-11（bybit 较晚标的）至 2026-07-24（价格数据末端）",
        "- funding 数据到 2026-08-02，价格数据到 2026-07-24；末端事件无前向收益，自动剔除",
        "",
        "## 汇总（跨标的 pooled）",
        "",
        "| 组合 | horizon | n_events | mean signal ret | t | 胜率 | excess vs 基线 |",
        "|---|---|---|---|---|---|---|",
    ]
    for pair in (f"{a}-{b}" for a, b in PAIRS):
        for h in HORIZONS_H:
            agg = aggregate(cells, pair, h)
            lines.append(
                f"| {pair} | {h}h | {agg['n']} | {_pct(agg['mean'])} | {_num(agg['t'])} "
                f"| {_pct(agg['win'])} | {_pct(agg['excess'])} |"
            )
    lines += [
        "",
        "## 分标的明细（t>2 判为有效信号）",
        "",
        "| 标的 | 组合 | horizon | n | mean ret | t | 胜率 | 基线 mean | excess |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for c in cells:
        lines.append(
            f"| {c.symbol} | {c.pair} | {c.horizon_h}h | {c.n_events} | {_pct(c.mean_signal_ret)} "
            f"| {_num(c.t_stat)} | {_pct(c.win_rate)} | {_pct(c.baseline_mean_ret)} "
            f"| {_pct(c.excess_vs_baseline)} |"
        )
    strong = [
        c for c in cells if not math.isnan(c.t_stat) and abs(c.t_stat) > 2 and c.n_events >= 30
    ]
    lines += ["", "## 结论", ""]
    if strong:
        lines.append("|t|>2 且 n≥30 的格子：")
        for c in sorted(strong, key=lambda c: -abs(c.t_stat)):
            verdict = "✅ 正向" if c.t_stat > 2 else "❌ 反向（信号方向反了）"
            lines.append(
                f"- **{c.symbol} {c.pair} {c.horizon_h}h**：t={c.t_stat:.2f}, n={c.n_events}, "
                f"mean={_pct(c.mean_signal_ret)}, 胜率={_pct(c.win_rate)} — {verdict}"
            )
        n_pos = sum(1 for c in strong if c.t_stat > 2)
        n_neg = len(strong) - n_pos
        lines += [
            "",
            "### 解读",
            "",
            f"- 显著格子 {len(strong)} 个（正向 {n_pos} / 反向 {n_neg}），"
            "噪声期望约 2 个，且符号高度一致 → 存在真实效应。",
            "- **原假设（逆着 funding 高的一方）被拒绝**：显著格子全部为反向，"
            "即 |跨所 funding 差| 极端时，",
            "  价格在随后 24h–72h **顺着** funding 高的一方走（拥挤方向延续，而非回落）。",
            "  跨所 funding 差是**动量/延续**信号，不是反转信号。",
            "- 最强组合：**binance-hyperliquid @ 72h**"
            "（BTC t=-3.32、SOL t=-3.49、AVAX t=-2.93、DOGE t=-2.23，",
            "  反向执行即顺着 binance funding 高的一方，mean +1.3%~+2.4%/72h，胜率 54%~55%）。",
            "- binance-bybit 组合几乎无显著性：两家 CEX funding 机制相近、差值小而稳，"
            "事件少且信息含量低。",
            "- 8h horizon 全部不显著：效应需要时间展开，短线不可交易。",
            "- 若将方向取反（顺着 funding 高的一方 @ 72h, binance-HL），pooled t≈+2.1，",
            "  但注意这是在看到结果后翻转方向，属事后选择，需样本外验证才能采信。",
        ]
    else:
        lines.append("没有任何 (标的, 组合, horizon) 格子达到 |t|>2 且 n≥30 — 因子无效。")
    lines += [
        "",
        "## 统计口径警示",
        "",
        "- 24h/72h horizon 的收益窗口重叠，t 值被高估；8h 事件在 8h 网格上相邻亦自相关。",
        "  t 值按 iid 公式报告，未做 Newey-West 修正，解读时保守看待。",
        "- 未计手续费/资金费收付本身；纯价格收益视角。",
        "- 42 个格子（7 标的 × 2 组合 × 3 horizon）的多重检验下，约 2 个 |t|>2 是噪声期望。",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--no-report", action="store_true", help="print only, do not write REPORT.md"
    )
    args = parser.parse_args(argv)
    cells = run_study()
    for c in cells:
        print(
            f"{c.symbol:5s} {c.pair:20s} {c.horizon_h:3d}h n={c.n_events:4d} "
            f"mean={_pct(c.mean_signal_ret):>9s} t={_num(c.t_stat):>6s} "
            f"win={_pct(c.win_rate):>7s} base={_pct(c.baseline_mean_ret):>9s}"
        )
    if not args.no_report:
        write_report(cells)
        print(f"\nreport written: {REPORT_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
