"""Build REPORT.md from grid_results/stability/rolling/plateau/controls CSVs."""
import sys

sys.path.insert(0, "/Users/mark/multica/quant-loop/research/kama_trend")

import json

import numpy as np
import pandas as pd

OUT = "/Users/mark/multica/quant-loop/research/kama_trend"

CAVEATS = """
## 8. 诚实性注解（必须阅读）

1. **赢家高度集中**: 3 个通过集全部是 BTC 4h、同一邻域(er5,f2,slow∈{20,30,50},lb10)——本质上是**一个**策略，不是三个独立发现。ETH/SOL 全周期、BTC 1h/1d 均无参数集通过。
2. **2024-26 的 t=1.94 主要由 2024 贡献**: BTC 4h top1 单年 t 为 2024:+2.7 / 2025:-0.1 / 2026:+0.2，即 2025 以来基本走平（滚动2y t 2026 年末仍有 +1.1，靠 2024 下半年的余量）。说"近年活着"勉强成立，说"近年在赚钱"不成立。
3. **ETH 4h 是反例**: KAMA top1 全样本 t=3.06、24-26 t=1.65、高原 100%，但同期 dumb MA20 t=3.12 —— KAMA 不优于 MA20，被第四条正确淘汰。说明 KAMA 在 ETH 4h 上的 alpha 只是普通趋势跟随。
4. **BTC 4h 上 KAMA vs MA20**: t 2.95 vs 1.84（MA20 24-26 t 仅 +0.39），差距真实存在，但 MA20 只是众多 dumb 基准之一，未与 MA50/MA100/donchian 等对比。
5. **t 值口径**: per-bar t 未做 Newey-West 自相关调整，1h/4h 收益存在正自相关，t 有系统性高估（全样本与对照同口径，相对比较仍有效）。
6. **费用假设**: 7bp 往返、taker 成交、无滑点。BTC 4h top1 共 455 次往返 ≈ 31.9% 累计费用拖累已计入。
7. **多重检验**: 864 个回测取 top，存在选择偏差；高原检查缓解但不消除。建议下一步对 BTC 4h (er5,f2,s30,lb10) 做 walk-forward 或 2024 前拟合/2024 后验证的样本外切分。
"""

grid = pd.read_csv(f"{OUT}/grid_results.csv")
stab = pd.read_csv(f"{OUT}/stability_yearly.csv")
roll = pd.read_csv(f"{OUT}/rolling_2y_t.csv")
plat = pd.read_csv(f"{OUT}/plateau.csv")
ctrl = pd.read_csv(f"{OUT}/controls.csv")
top_map = json.load(open(f"{OUT}/top_map.json"))
top_items = [(k.split("_")[0], k.split("_")[1], v) for k, v in top_map.items()]

L = []
L.append("# KAMA 斜率多头策略 — 参数网格寻优报告\n")
L.append("数据: `data/perp_15m/{BTC,ETH,SOL}USDT_15m.parquet` 聚合到 1h/4h/1d。"
         "BTC 自 2019-09, ETH 自 2019-11, SOL 自 2020-09, 均至 2026-07-24。\n")
L.append("策略: KAMA(er,fast,slow) 斜率(lb 根 bar)>0 → 做多, ≤0 → 平仓; 信号在 bar 收盘产生, 下一根 bar 生效(无前视)。"
         "费用 7bp 往返(每边 3.5bp)。t 值 = per-bar mean/std·√N（净收益）。\n")
L.append("网格: er∈{5,10,15,20} × fast∈{2,3} × slow∈{20,30,50} × lb∈{1,3,5,10} = 96 组 × 3 周期 × 3 标的 = 864 个回测。\n")

L.append("\n## 1. Top5 参数集（按全样本 t）\n")
for sym, tf, tops in top_items:
    g = grid[(grid.symbol == sym) & (grid.tf == tf)].nlargest(5, "t")
    L.append(f"\n### {sym} {tf}\n")
    L.append("| # | er | fast | slow | lb | 全样本t | 往返次数 | 年化(复利) |")
    L.append("|---|----|------|------|----|--------|-----------|-----------|")
    for i, (_, x) in enumerate(g.iterrows(), 1):
        ann = (1 + x.ann_ret) ** ({"1h": 24 * 365, "4h": 6 * 365, "1d": 365}[tf]) - 1
        L.append(f"| {i} | {x.er} | {x.fast} | {x.slow} | {x.lb} | {x.t:.2f} | {x.n_rt} | {ann*100:.1f}% |")

L.append("\n## 2. 稳定性：逐年 t 分解（top1 参数集）\n")
L.append("判定标准: **2024-2026 合并 t>1.5 且均值为正** 才算近年活着。\n")
for sym, tf, tops in top_items:
    p = tops[0]
    s = stab[(stab.symbol == sym) & (stab.tf == tf) &
             (stab.er == p[0]) & (stab.fast == p[1]) & (stab.slow == p[2]) & (stab.lb == p[3])]
    yrs = s[s.year != 20242026].sort_values("year")
    rec = s[s.year == 20242026].iloc[0]
    line = " | ".join(f"{int(y.year)}:{y.t:+.1f}" for y in yrs.itertuples())
    verdict = "✅ 活" if (rec.t > 1.5 and rec["mean"] > 0) else "❌ 死"
    L.append(f"- **{sym} {tf}** top1=(er{p[0]},f{p[1]},s{p[2]},lb{p[3]}): {line} "
             f"‖ 2024-26: t={rec.t:+.2f}, mean={rec['mean']:+.6f} → {verdict}")

L.append("\n## 3. 稳定性：top5 中 2024-26 表现一览\n")
L.append("| 标的 | 周期 | 参数(er,f,s,lb) | 全样本t | 2024-26 t | 2024-26 mean>0 |")
L.append("|------|------|-----------------|--------|-----------|----------------|")
for sym, tf, tops in top_items:
    g = grid[(grid.symbol == sym) & (grid.tf == tf)].nlargest(5, "t")
    for _, x in g.iterrows():
        s = stab[(stab.symbol == sym) & (stab.tf == tf) & (stab.er == x.er) &
                 (stab.fast == x.fast) & (stab.slow == x.slow) & (stab.lb == x.lb) & (stab.year == 20242026)]
        r = s.iloc[0]
        L.append(f"| {sym} | {tf} | ({x.er},{x.fast},{x.slow},{x.lb}) | {x.t:.2f} | {r.t:+.2f} | {'是' if r['mean']>0 else '否'} |")

L.append("\n## 4. 滚动 2 年窗口 t 轨迹（每年末采样, top1 参数集）\n")
for sym, tf, tops in top_items:
    p = tops[0]
    s = roll[(roll.symbol == sym) & (roll.tf == tf) &
             (roll.er == p[0]) & (roll.fast == p[1]) & (roll.slow == p[2]) & (roll.lb == p[3])].sort_values("year")
    line = " | ".join(f"{int(y.year)}:{y.roll2y_t:+.1f}" for y in s.itertuples())
    L.append(f"- **{sym} {tf}** (er{p[0]},f{p[1]},s{p[2]},lb{p[3]}): {line}")

L.append("\n## 5. 参数高原检查（top5 邻域 ±1 步为 t>0 的比例）\n")
L.append("邻域 = 四个维度各 ±1 步(在网格内), 最多 8 个邻居。frac≥0.6 视为高原。\n")
L.append("| 标的 | 周期 | 参数(er,f,s,lb) | 邻居数 | t>0 比例 | 邻居 t 值 |")
L.append("|------|------|-----------------|--------|----------|-----------|")
for _, x in plat.iterrows():
    flag = " ✅" if x.frac_pos >= 0.6 else " ⚠️"
    L.append(f"| {x.symbol} | {x.tf} | ({x.er},{x.fast},{x.slow},{x.lb}) | {x.n_nb} | {x.frac_pos:.0%}{flag} | {x.nb_t} |")

L.append("\n## 6. 对照实验：buy-hold / MA20\n")
L.append("| 标的 | 周期 | 对照 | 全样本t | 2024-26 t |")
L.append("|------|------|------|--------|-----------|")
for _, x in ctrl.iterrows():
    L.append(f"| {x.symbol} | {x.tf} | {x.ctrl} | {x.t:.2f} | {x.t_2024_26:+.2f} |")

# ---- final verdict ----
L.append("\n## 7. 最终判定\n")
L.append("标准: (全样本 t>2) AND (2024-26 t>1.5 且方向为正) AND (邻域≥60%为正) AND (显著优于 MA20, 即全样本 t 至少高 1.0)。\n")
winners = []
for sym, tf, tops in top_items:
    g = grid[(grid.symbol == sym) & (grid.tf == tf)].nlargest(5, "t")
    ma_t = ctrl[(ctrl.symbol == sym) & (ctrl.tf == tf) & (ctrl.ctrl == "ma20")].iloc[0].t
    for _, x in g.iterrows():
        s = stab[(stab.symbol == sym) & (stab.tf == tf) & (stab.er == x.er) &
                 (stab.fast == x.fast) & (stab.slow == x.slow) & (stab.lb == x.lb) & (stab.year == 20242026)].iloc[0]
        pl = plat[(plat.symbol == sym) & (plat.tf == tf) & (plat.er == x.er) &
                  (plat.fast == x.fast) & (plat.slow == x.slow) & (plat.lb == x.lb)].iloc[0]
        ok = (x.t > 2) and (s.t > 1.5 and s["mean"] > 0) and (pl.frac_pos >= 0.6) and (x.t > ma_t + 1.0)
        if ok:
            winners.append(f"{sym} {tf} (er{x.er},f{x.fast},s{x.slow},lb{x.lb}): t={x.t:.2f}, 24-26t={s.t:.2f}, 高原={pl.frac_pos:.0%}, MA20t={ma_t:.2f}")

if winners:
    L.append("**结论: 可以继续。** 满足全部标准的参数集:\n")
    for w in winners:
        L.append(f"- {w}")
else:
    L.append("**结论: 不用继续。** 无任何参数集同时满足全部四条标准。\n")
    # explain why: show the binding constraint per (sym,tf) top1
    L.append("\n各 (标的,周期) top1 的失败原因:\n")
    for sym, tf, tops in top_items:
        p = tops[0]
        x = grid[(grid.symbol == sym) & (grid.tf == tf) & (grid.er == p[0]) &
                 (grid.fast == p[1]) & (grid.slow == p[2]) & (grid.lb == p[3])].iloc[0]
        s = stab[(stab.symbol == sym) & (stab.tf == tf) & (stab.er == p[0]) &
                 (stab.fast == p[1]) & (stab.slow == p[2]) & (stab.lb == p[3]) & (stab.year == 20242026)].iloc[0]
        pl = plat[(plat.symbol == sym) & (plat.tf == tf) & (plat.er == p[0]) &
                  (plat.fast == p[1]) & (plat.slow == p[2]) & (plat.lb == p[3])].iloc[0]
        ma_t = ctrl[(ctrl.symbol == sym) & (ctrl.tf == tf) & (ctrl.ctrl == "ma20")].iloc[0].t
        fails = []
        if x.t <= 2: fails.append(f"全样本t={x.t:.2f}≤2")
        if not (s.t > 1.5 and s["mean"] > 0): fails.append(f"2024-26 t={s.t:+.2f}")
        if pl.frac_pos < 0.6: fails.append(f"高原={pl.frac_pos:.0%}<60%")
        if x.t <= ma_t + 1.0: fails.append(f"未显著优于MA20(t {x.t:.2f} vs {ma_t:.2f})")
        L.append(f"- {sym} {tf}: {'; '.join(fails)}")

with open(f"{OUT}/REPORT.md", "w") as fh:
    fh.write("\n".join(L) + "\n" + CAVEATS)
print("REPORT.md written,", len(L), "lines; winners:", len(winners))
