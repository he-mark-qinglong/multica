#!/usr/bin/env python3
"""Generate the first periodic self-review report.

Wires together the evaluation stack's "self-reflection" layer:

  1. Lifecycle state machine — instantiates the two landed strategies at
     their true states (cash_carry_combo → PAPER, hedged_grid_v1 →
     BACKTESTING), appends to the jsonl audit log, and snapshots states to
     ``strategies/lifecycle_state.json``.
  2. Decay monitor — re-runs the hedged-grid backtest (fast, deterministic)
     to recover the combo equity curve, and runs
     ``_shared/validation/decay_monitor.monitor_decay`` on its daily
     returns (signal = trailing 20d mean return, no look-ahead).
  3. Self-review — compares backtest-expected vs paper-actual metrics for
     cash_carry (paper ledger 2023-10-31 → 2026-06-30, 974 days), records
     the three falsification events of this round as belief downgrades and
     methodological lessons, and renders a self-contained HTML report to
     ``reports/self_review_2026-08-02.html``.

Usage:
    python scripts/gen_first_review.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path("/Users/mark/multica/quant-loop")
sys.path.insert(0, str(ROOT))

import pandas as pd

from _shared.portfolio.lifecycle import (
    LifecycleManager,
    LifecycleState,
    StrategyMetrics,
)
from _shared.validation.decay_monitor import monitor_decay
from _shared.validation.self_review import (
    Belief,
    BeliefUpdate,
    Lesson,
    MetricSet,
    SelfReviewReport,
    render_html,
    review_strategy,
    update_belief,
    DeviationLevel,
)

CASH_ID = "cash_carry_combo_v1_20260731"
GRID_ID = "hedged_grid_v1_20260802"

CASH_DIR = ROOT / "strategies" / CASH_ID
GRID_DIR = ROOT / "strategies" / GRID_ID
PAPER_LEDGER = CASH_DIR / "paper_run" / "results-ledger" / "daily_metrics.csv"

STATE_JSON = ROOT / "strategies" / "lifecycle_state.json"
AUDIT_JSONL = ROOT / "strategies" / "lifecycle_audit.jsonl"
REPORT_HTML = ROOT / "reports" / "self_review_2026-08-02.html"

REVIEW_TS = "2026-08-02"
WINDOW = "2026-07-31 ~ 2026-08-02（评价体系自省层首轮复盘）"


# ---------------------------------------------------------------------------
# 1. Lifecycle wiring
# ---------------------------------------------------------------------------

def wire_lifecycle() -> dict[str, str]:
    """Instantiate both strategies at their true funnel states.

    Idempotent per fresh manager; the audit jsonl is append-only so a
    re-run records a second (identical) attempt chain — acceptable for an
    audit trail, and the state snapshot is rewritten each run.
    """
    mgr = LifecycleManager(audit_path=AUDIT_JSONL)

    cash_bt = json.loads((CASH_DIR / "results.json").read_text())["combo"]
    mgr.register(CASH_ID)
    mgr.transition(CASH_ID, LifecycleState.BACKTESTING)
    mgr.transition(
        CASH_ID,
        LifecycleState.PAPER,
        StrategyMetrics(
            sharpe=None,  # Sharpe not in results.json; PAPER gate is unconditional
            max_drawdown=cash_bt["max_drawdown_pct"],
        ),
    )

    grid_bt = json.loads((GRID_DIR / "results.json").read_text())["combo"]
    mgr.register(GRID_ID)
    mgr.transition(
        GRID_ID,
        LifecycleState.BACKTESTING,
        StrategyMetrics(max_drawdown=grid_bt["max_drawdown_pct"]),
    )

    states = {
        CASH_ID: mgr.state(CASH_ID).value,
        GRID_ID: mgr.state(GRID_ID).value,
    }
    snapshot = {
        "generated_at": REVIEW_TS,
        "audit_log": str(AUDIT_JSONL.relative_to(ROOT)),
        "states": states,
        "evidence": {
            CASH_ID: "backtest verdict PASS (Calmar 0.82) + paper runner "
            "974d ledger through 2026-06-30, not killed",
            GRID_ID: "backtest verdict PASS (combo Calmar 1.03 >= gate 1.0); "
            "paper runner not yet wired",
        },
    }
    STATE_JSON.write_text(json.dumps(snapshot, indent=2, ensure_ascii=False))
    return states


# ---------------------------------------------------------------------------
# 2. Paper-actual metrics for cash_carry from the paper ledger
# ---------------------------------------------------------------------------

def cash_paper_actual() -> MetricSet:
    df = pd.read_csv(PAPER_LEDGER, parse_dates=["date"])
    eq = df["equity_usd"].astype(float)
    days = max((df["date"].iloc[-1] - df["date"].iloc[0]).days, 1)
    ann = (eq.iloc[-1] / eq.iloc[0] - 1.0) / days * 365.0
    dd = eq / eq.cummax() - 1.0
    max_dd = float(dd.min())
    calmar = ann / abs(max_dd) if max_dd < 0 else float("inf")
    pf = float(df["profit_factor_lifetime"].iloc[-1])
    return MetricSet(
        annualized_return=ann,
        max_drawdown=max_dd,
        calmar=calmar,
        profit_factor=pf,
        fill_rate=None,  # ledger has no fill-rate column — honestly absent
    )


# ---------------------------------------------------------------------------
# 3. Decay check on the hedged-grid combo returns
# ---------------------------------------------------------------------------

def grid_decay_status() -> tuple[str, dict[str, float]]:
    """Re-run the (deterministic) grid backtest and decay-check the combo.

    Signal = trailing 20d mean of daily combo returns, shifted 1 bar
    (no look-ahead); forward = next-day return. strategy_returns is the
    actual daily return stream for the rolling-Sharpe cross-check.
    """
    sys.path.insert(0, str(GRID_DIR))
    import strategy as grid_strategy  # noqa: E402  (path set above)

    cfg = grid_strategy.GridConfig.from_json(GRID_DIR / "config.json")
    curves = {}
    for sym_cfg in cfg.symbols:
        bars, funding = grid_strategy.load_symbol_data(sym_cfg.symbol)
        res = grid_strategy.run_symbol(bars, funding, sym_cfg, cfg.initial_capital)
        curves[sym_cfg.symbol] = res["equity"] / cfg.initial_capital - 1.0
    combo = pd.DataFrame(curves).ffill().dropna().mean(axis=1)
    daily_ret = combo.resample("1D").last().dropna().pct_change().dropna()

    signal = daily_ret.rolling(20).mean().shift(1)
    forward = daily_ret.shift(-1)
    rep = monitor_decay(
        signal,
        forward,
        strategy_returns=daily_ret,
        window=60,
        recent=10,
    )
    diag = {
        "recent_ic": rep.recent_ic,
        "early_ic": rep.early_ic,
        "ic_slope_per_year": rep.ic_slope_per_year,
        "recent_sharpe": rep.recent_sharpe,
    }
    label = (
        f"{rep.status}（recent IC {rep.recent_ic:+.3f}，"
        f"IC 斜率 {rep.ic_slope_per_year:+.3f}/yr，"
        f"半衰期 {('%.2f yr' % rep.half_life_years) if rep.half_life_years else 'n/a'}，"
        f"recent Sharpe {rep.recent_sharpe:+.2f}）"
    )
    return label, diag


# ---------------------------------------------------------------------------
# 4. Beliefs seeded with this round's falsification history + lessons
# ---------------------------------------------------------------------------

def cash_belief() -> Belief:
    """cash_carry belief: downgraded by the funding-carry look-ahead event."""
    return update_belief(
        Belief(confidence=1.0),
        DeviationLevel.BREACH,
        "[2026-07-31] funding carry 前视偏差：A 模型（事后已知 funding）"
        "avg +45.7bp / PF 2.04，B 模型（可成交口径）-17.6bp / PF 0.76；"
        "以悲观口径为准，原盈利结论被推翻。该 combo 的 carry 前提受损。",
        ts="2026-07-31",
    )


def grid_belief() -> Belief:
    """hedged_grid belief: downgraded by the prototype's three engine bugs."""
    return update_belief(
        Belief(confidence=1.0),
        DeviationLevel.BREACH,
        "[2026-08-02] 原型回测引擎三处 bug（幻影交易 / 双重计费 / 前 30 天"
        "无对冲），原型 -12.6%/yr 与修复后 +4.35%/yr（BTC Calmar 2.57）"
        "的差异说明：引擎未守恒前，收益数字不可信。",
        ts="2026-08-02",
    )


def lessons() -> tuple[Lesson, ...]:
    return (
        Lesson(
            lesson_id="L-20260731-01",
            date="2026-07-31",
            strategy_id=CASH_ID,
            event=(
                "funding carry 前视偏差：A 模型按事后已知的 funding 结算，"
                "avg +45.7bp、PF 2.04；改用逐事件可成交的 B 模型后 "
                "-17.6bp、PF 0.76。两次'以为盈利'被双口径复算诚实推翻。"
            ),
            lesson=(
                "结算类收益必须同时实现'理想口径'与'可成交口径'双模型："
                "两口径剧烈分歧本身就是前视偏差检测器；对外结论一律以悲观"
                "口径为准，乐观口径只作上界参考。"
            ),
        ),
        Lesson(
            lesson_id="L-20260802-01",
            date="2026-08-02",
            strategy_id=GRID_ID,
            event=(
                "对冲网格原型三处引擎 bug：幻影交易（无对手价成交）、"
                "费用双重计费、前 30 天无对冲真空。修复前 -12.6%/yr，"
                "修复后 +4.35%/yr（BTC Calmar 2.57，组合 1.03）。"
            ),
            lesson=(
                "先守恒、后评价：回测引擎须先通过守恒检查（成交必有对手价、"
                "费用只计一次、对冲无真空期），才有资格解读收益；修 bug "
                "前后的收益差不是 alpha，是噪声。"
            ),
        ),
        Lesson(
            lesson_id="L-20260802-02",
            date="2026-08-02",
            strategy_id="",  # 工作区级
            event=(
                "跨所 funding 因子方向假设被拒：先验为 contrarian（高 "
                "funding 反转），t 值为正不支持；实测为动量方向，"
                "t = -3.49。"
            ),
            lesson=(
                "方向性先验被数据拒绝时，如实记录拒绝证据并反向检验；"
                "被拒的假设是资产不是污点——写入 lessons 防止未来重复"
                "提出同一假设。"
            ),
        ),
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    states = wire_lifecycle()

    cash_bt = json.loads((CASH_DIR / "results.json").read_text())["combo"]
    grid_bt = json.loads((GRID_DIR / "results.json").read_text())["combo"]

    decay_label, _diag = grid_decay_status()

    cash_review = review_strategy(
        strategy_id=CASH_ID,
        lifecycle_state=states[CASH_ID],
        window=WINDOW,
        expected=MetricSet(
            annualized_return=cash_bt["annualized_return"],
            max_drawdown=cash_bt["max_drawdown_pct"],
            calmar=cash_bt["calmar"],
        ),
        actual=cash_paper_actual(),
        belief=cash_belief(),
        ts=REVIEW_TS,
        notes=(
            "paper runner 974 天 ledger 已跑通（2023-10-31 → 2026-06-30），未触发 kill。",
            "paper 与回测的口径差异：paper 含逐日费用/滑点记账，回测为事件口径。",
        ),
    )

    grid_review = review_strategy(
        strategy_id=GRID_ID,
        lifecycle_state=states[GRID_ID],
        window=WINDOW,
        expected=MetricSet(
            annualized_return=grid_bt["annualized_return"],
            max_drawdown=grid_bt["max_drawdown_pct"],
            calmar=grid_bt["calmar"],
        ),
        actual=MetricSet(),  # 尚未接 paper runner——无实际值可对比
        belief=grid_belief(),
        ts=REVIEW_TS,
        decay_status=decay_label,
        notes=(
            "仅有回测证据（combo Calmar 1.03 过门槛 1.0）；paper runner 未接线，"
            "偏差分级待 paper 数据。",
            "信号衰减检查基于重跑回测的组合日收益（信号=过去 20 日均收益，无前视）。",
        ),
    )

    report = SelfReviewReport(
        title="quant-loop 自省报告 #1 — 评价体系自我反思层",
        generated_at=f"{REVIEW_TS}T00:00:00+00:00",
        window=WINDOW,
        strategies=(cash_review, grid_review),
        lessons=lessons(),
    )
    REPORT_HTML.parent.mkdir(parents=True, exist_ok=True)
    REPORT_HTML.write_text(render_html(report), encoding="utf-8")

    print(f"lifecycle states: {states}")
    print(f"  snapshot → {STATE_JSON}")
    print(f"  audit    → {AUDIT_JSONL}")
    print(f"cash_carry: overall={cash_review.assessment.overall.value}, "
          f"belief={cash_review.belief.confidence:.2f}")
    print(f"hedged_grid: overall={grid_review.assessment.overall.value}, "
          f"belief={grid_review.belief.confidence:.2f}, decay={decay_label}")
    print(f"report → {REPORT_HTML}")


if __name__ == "__main__":
    main()
