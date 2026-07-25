# W4-S4 — T07/T08 执行卡（全历史回测 + fee shock + WF runner window 0）

- slice: `w4-s4`（隶属 workstream `w4-signal-enhance-h3`）
- 日期: 2026-07-25
- 卡片: **W4-T07**、**W4-T08**（id 沿用 round-1 编号，供跨片依赖引用）
- 通用约束:
  - python 一律 `/Users/mark/sdk/mamba-envs/trading/bin/python3`（默认 python3 缺 pyarrow）。
  - `FH` = `/Users/mark/multica/quant-loop/research/swarm/2026-07-25/signal-enhance-h3/full_history/`
    （执行时该目录已由 T01-T04 建好；若不存在说明依赖未完成，停下报错）。
  - 只写 `FH/` 下的本卡产物文件；`quant-loop/strategies/`、`quant-loop/_shared/`、
    `H3-variants-h1h2h4/`、`H3-baseline-repro/`、`data/` 一律只读。
  - 不做任何 git 操作。

---

## 上游接口契约（T02/T04 产物，T07/T08 直接消费——已按 round-1 规格锁定）

以下两个模块由别的任务卡写好，本 slice 的代码只 import、不修改：

**`FH/se_h3_common.py`（T02）** 须暴露：

```python
load_aligned_data()  # -> (d1m: dict[str, DataFrame], funding: dict[str, Series], common_idx: DatetimeIndex)
                     # len(common_idx) == 2448219, span 2021-11-20 16:01 -> 2026-07-17 19:39
load_se_h3_config()  # -> cfg dict（H3 config + 锁定覆盖 slope_lookback=4, adverse_stop_z=0.7, regime_break=9.0）
# 另从 H3-variants-h1h2h4/run_btcsol_variants_fixed.py 原位 re-export：
portfolio_metrics    # fixed runner L220: (result, idx, cfg) -> (metrics dict, equity Series)
fee_shock_metrics    # fixed runner L313: (equity, trades, pair_rt_bps) -> dict
```

**`FH/se_h3_loop.py`（T04）** 须暴露：

```python
run_se_h3(d1m, cfg, funding)  # -> result dict，镜像 base run_backtest 返回结构：
  # result["portfolio"]: keys "equity" (list[float]), "bar_return", "n_bars"
  # result["per_pair"]:  list[dict]，每个含 "trades": list[dict]
  # 每个 trade 至少含: pnl_pct (net), gross_pct, exit_ts, exit_reason
```

**质量闸门**：两张卡开工前，orchestrator 须确认 T06（loop parity）已绿。卡内自检为
`import se_h3_common, se_h3_loop` 无错；import 失败 = 依赖未完成，立即停止上报，不得自行补写上游模块。

---

## W4-T07 — 全历史回测 runner + 4/24/60bps fee shock

- **目标**: 全历史（2 448 219 bars）跑组合候选（slope 过滤 + adverse stop + regime_break 9.0），
  产出全历史指标 + 三档 fee shock 证据，供 T15 聚合与主线判决。
- **est**: 30 min（写脚本 ~8 min + 运行 10-25 min，贴上限，见断点续跑协议）
- **machine**: **mac**（数据 parquet 只在 mac 仓库；不要派到 105）

### 读（全部已验证存在）

- `FH/se_h3_common.py`、`FH/se_h3_loop.py`（上游契约，见上）
- `quant-loop/research/swarm/2026-07-25/H3-variants-h1h2h4/run_btcsol_variants_fixed.py`（只读；
  `portfolio_metrics` L220-239、`fee_shock_metrics` L313-346、fee 档位 L417-422、
  equity 落 csv 写法 L491-495）
- 数据（由 `load_aligned_data()` 间接读）: `quant-loop/data/perp_1m/{BTC,SOL}USDT_1m.parquet`、
  `quant-loop/data/funding/{BTC,SOL}USDT.parquet`

### 写（只写这些）

- `FH/run_full_history.py`（新建脚本）
- `FH/results/checkpoints/phase2_result.pkl`（断点续跑检查点）
- `FH/results/se_h3_full_history_metrics.json`
- `FH/results/se_h3_trades.csv`
- `FH/results/se_h3_equity_daily.csv`
- `FH/results/se_h3_fee_shock.json`
- `FH/results/run_full_history.log`（nohup 输出）
- `FH/results/T07_STATUS.md`（仅当 30 min 预算内跑不完时写，见下）

### 步骤

1. `cd FH`，自检：`/Users/mark/sdk/mamba-envs/trading/bin/python3 -c "import se_h3_common, se_h3_loop"`。
   失败即停（依赖未就绪）。
2. 写 `run_full_history.py`，分 6 个 phase，每 phase 打印 `[T07] phase N done in Xs`：

```python
import json, math, pickle, sys, time
from pathlib import Path
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from se_h3_common import (load_aligned_data, load_se_h3_config,
                          portfolio_metrics, fee_shock_metrics)
from se_h3_loop import run_se_h3

FH = Path(__file__).resolve().parent
RES = FH / "results"; CKPT = RES / "checkpoints"
CKPT.mkdir(parents=True, exist_ok=True)
FEE_LEVELS = (("inhouse_4bps_rt", 4.0), ("freqtrade_24bps_rt", 24.0),
              ("backtrader_60bps_rt", 60.0))   # 与 fixed runner L417-422 一致

# phase 1: 数据 + config（不做检查点，约 1 min）
d1m, funding, common_idx = load_aligned_data()
cfg = load_se_h3_config()
assert len(common_idx) == 2448219, len(common_idx)

# phase 2: 全历史回测（10-25 min，唯一长 phase）→ 立即 pickle 检查点
if (CKPT / "phase2_result.pkl").exists():
    res = pickle.loads((CKPT / "phase2_result.pkl").read_bytes())
else:
    t0 = time.time()
    res = run_se_h3(d1m, cfg, funding)
    (CKPT / "phase2_result.pkl").write_bytes(pickle.dumps(res))
    print(f"[T07] phase 2 done in {time.time()-t0:.0f}s", flush=True)

# phase 3: portfolio_metrics（fixed runner 原函数）→ equity daily csv
full_metrics, equity = portfolio_metrics(res, common_idx, cfg)
daily_eq = equity.resample("1D").last().dropna()
daily_eq.to_frame("equity").to_csv(RES / "se_h3_equity_daily.csv")

# phase 4: trades 展平落 csv
trades = [t for pp in res["per_pair"] for t in pp["trades"]]
pd.DataFrame(trades).to_csv(RES / "se_h3_trades.csv", index=False)

# phase 5: fee shock 三档（fixed runner 原函数，per_trade_fraction 用默认 0.005）
fee_sens = {label: fee_shock_metrics(equity, trades, rt) for label, rt in FEE_LEVELS}
(RES / "se_h3_fee_shock.json").write_text(json.dumps(fee_sens, indent=2, default=float))

# phase 6: 汇总 json（config 快照 + data_span + n_bars，供 T15/审计）
summary = {
    "source_script": "run_full_history.py",
    "n_bars": int(len(common_idx)),
    "data_span": [str(common_idx[0]), str(common_idx[-1])],
    "config_snapshot": cfg,
    "full_history": full_metrics,
}
(RES / "se_h3_full_history_metrics.json").write_text(json.dumps(summary, indent=2, default=float))
print("[T07] ALL DONE", flush=True)
```

3. **断点续跑协议（必须照做，本卡贴近 30 min 上限）**：
   - 用 nohup 后台起跑：
     `cd FH && nohup /Users/mark/sdk/mamba-envs/trading/bin/python3 run_full_history.py > results/run_full_history.log 2>&1 &`，记下 PID。
   - 每 ~2 min `tail -5 results/run_full_history.log` 并用 `ps -p <PID> -o etime,time` 确认进程存活
     （phase 2 内部无日志输出，活进程 CPU time 持续增长即正常，不要因日志静默而 kill）。
   - **若 30 min 预算耗尽而进程未结束：绝不 kill**。写 `results/T07_STATUS.md`（内容：PID、
     已完成 phase、已存在产物清单、log 尾部 5 行），以 INCOMPLETE 上报；后续 agent 可直接
     `tail results/run_full_history.log` 等待完成后再跑验收命令。
   - 若进程异常死亡：直接重跑同一命令即可——phase 2 检查点命中会跳过已完成的长 phase
     （phase 2 本身不可中断，死了只能重跑这一段，这是预期行为）。
4. 跑完做 sanity：打印 `n_trades`。预期远小于 baseline 的 40 963（过滤器生效；2024 子样本约 704 笔/年
   量级）。`n_trades >= 40963` 说明过滤没挂上，停下排查 `load_se_h3_config` 的锁定参数，不要硬过验收。

### 验收（可运行命令，全部须通过）

```bash
cd /Users/mark/multica/quant-loop/research/swarm/2026-07-25/signal-enhance-h3/full_history && \
/Users/mark/sdk/mamba-envs/trading/bin/python3 - <<'EOF'
import json, math
import pandas as pd
m = json.load(open("results/se_h3_full_history_metrics.json"))
assert m["n_bars"] == 2448219, m["n_bars"]
assert m["data_span"] == ["2021-11-20 16:01:00", "2026-07-17 19:39:00"], m["data_span"]
assert "config_snapshot" in m and m["full_history"]["n_trades"] > 0
fs = json.load(open("results/se_h3_fee_shock.json"))
assert set(fs) == {"inhouse_4bps_rt", "freqtrade_24bps_rt", "backtrader_60bps_rt"}, set(fs)
for k in fs:
    assert math.isfinite(fs[k]["sharpe_daily_resampled"]), k
n_csv = len(pd.read_csv("results/se_h3_trades.csv"))
assert n_csv == m["full_history"]["n_trades"], (n_csv, m["full_history"]["n_trades"])
print("T07 ACCEPT OK  n_trades =", n_csv,
      " fee-shock 60bps sharpe =", round(fs["backtrader_60bps_rt"]["sharpe_daily_resampled"], 4))
EOF
```

预期输出 `T07 ACCEPT OK n_trades = <正整数> ...`；log 最后一行为 `[T07] ALL DONE`。

### 依赖

- 前置: T02、T03、T04（模块契约），T06 绿（质量闸门）。
- 被依赖: T15（聚合器读本卡全部产物）。

---

## W4-T08 — WF 窗口 runner `run_wf_window.py` + window 0 自验 + 边界断言

- **目标**: 写可复用的单窗口 walk-forward runner（T09-T14 将用同一脚本跑 window 1-6），
  内置 7 组 ISO 边界硬断言防数据漂移，并自验 window 0。
- **est**: 15 min（写脚本 ~10 min + window 0 运行 <5 min）
- **machine**: **mac**（需数据 parquet。仅当已在 105 上核实
  `quant-loop/data/perp_1m/BTCUSDT_1m.parquet` 存在且 `full_history/` 目录同步过时，才可派 105；
  否则一律 mac）

### 读（全部已验证存在）

- `FH/se_h3_common.py`、`FH/se_h3_loop.py`（上游契约，同上）
- `run_btcsol_variants_fixed.py` L242-259（窗口算法，只读参照）：expanding-train，
  train 525600 / test 262800 / step 262800，从对齐索引第 525600 根起切 test 段，
  2 448 219 bars 恰得 7 窗；L262-275（**信号只在 test 切片内建**、funding 按窗口裁剪、
  cost 覆盖、`portfolio_metrics` 用法）。
- `H3-baseline-repro/metrics.json` → `walk_forward_oos.per_window`（只读，边界断言表来源；
  注意该文件 per_window 条目**没有** `test_bars` 键，只有 ISO 字符串）。

### 写（只写这些）

- `FH/run_wf_window.py`（新建）
- `FH/results/se_h3_wf_window_0.json`
- `FH/results/se_h3_wf_trades_0.csv`

脚本对 `--window K` 只许写 `se_h3_wf_window_{K}.json` / `se_h3_wf_trades_{K}.csv` 两个文件
（T09-T14 复用时文件互不相交）；重跑同一 K 允许覆盖自己的产物。

### 步骤

1. `cd FH`，自检 import（同 T07 步骤 1），失败即停。
2. 写 `run_wf_window.py`：

```python
import argparse, json, math, sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from se_h3_common import load_aligned_data, load_se_h3_config, portfolio_metrics
from se_h3_loop import run_se_h3

FH = Path(__file__).resolve().parent
RES = FH / "results"; RES.mkdir(parents=True, exist_ok=True)

TRAIN, TEST, STEP = 525600, 262800, 262800   # fixed runner L44-48 锁定值

# 边界断言表：逐字来自 H3-baseline-repro/metrics.json walk_forward_oos.per_window
EXPECTED = [
    ("2022-11-20 16:01:00", "2023-05-22 04:00:00"),
    ("2023-05-22 04:01:00", "2023-11-20 16:00:00"),
    ("2023-11-20 16:01:00", "2024-05-21 04:00:00"),
    ("2024-05-21 04:01:00", "2024-11-19 16:00:00"),
    ("2024-11-19 16:01:00", "2025-05-21 04:00:00"),
    ("2025-05-21 04:01:00", "2025-11-19 16:00:00"),
    ("2025-11-19 16:01:00", "2026-05-21 04:00:00"),
]

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--window", type=int, required=True)
    k = ap.parse_args().window

    d1m, funding, common_idx = load_aligned_data()
    cfg = load_se_h3_config()
    n_bars = len(common_idx)
    assert n_bars == 2448219, n_bars

    windows = []
    te_s = TRAIN
    while te_s + TEST <= n_bars:            # fixed runner L254-257 算法
        windows.append((te_s, te_s + TEST))
        te_s += STEP
    assert len(windows) == 7, len(windows)
    if not (0 <= k < 7):
        raise SystemExit(f"window {k} out of range 0..6")

    te_s, te_e = windows[k]
    start_iso, end_iso = str(common_idx[te_s]), str(common_idx[te_e - 1])
    exp = EXPECTED[k]
    if (start_iso, end_iso) != exp:          # 数据漂移防护：边界不符即 exit 1
        print(f"[T08] BOUNDARY MISMATCH window {k}: got {(start_iso, end_iso)} want {exp}")
        raise SystemExit(1)

    d_win = {sym: df.iloc[te_s:te_e].copy() for sym, df in d1m.items()}   # 镜像 L263
    start_ts, end_ts = common_idx[te_s], common_idx[te_e - 1]
    funding_win = {sym: f[(f.index >= start_ts) & (f.index <= end_ts)].copy()
                   for sym, f in funding.items()}                          # 镜像 L266-269
    cfg_cost = json.loads(json.dumps(cfg))
    cfg_cost["fees_bps_per_side"] = 1.0
    cfg_cost["slippage_bps_per_side"] = 1.0                                # 镜像 L270-272

    t0 = time.time()
    res = run_se_h3(d_win, cfg_cost, funding_win)                          # 信号只在切片内建
    metrics_win, _eq = portfolio_metrics(res, common_idx[te_s:te_e], cfg)  # 镜像 L274-275
    n_trades = sum(len(pp["trades"]) for pp in res["per_pair"])

    trades = [t for pp in res["per_pair"] for t in pp["trades"]]
    import pandas as pd
    pd.DataFrame(trades).to_csv(RES / f"se_h3_wf_trades_{k}.csv", index=False)

    out = {
        "window_id": k,
        "test_bars": [int(te_s), int(te_e)],
        "test_start_iso": start_iso,
        "test_end_iso": end_iso,
        "n_trades": n_trades,
        "sharpe_daily_resampled": metrics_win["sharpe_daily_resampled"],
        "annualized_return_daily_resampled": metrics_win["annualized_return_daily_resampled"],
        "max_drawdown_pct": metrics_win["max_drawdown_pct_daily_method"],
        "profit_factor": metrics_win["profit_factor_daily_method"],
        "elapsed_sec": round(time.time() - t0, 1),
        "source_script": "run_wf_window.py",
    }
    (RES / f"se_h3_wf_window_{k}.json").write_text(json.dumps(out, indent=2, default=float))
    print(f"[T08] window {k} OK trades={n_trades} sharpe={out['sharpe_daily_resampled']:.3f}", flush=True)

if __name__ == "__main__":
    main()
```

3. 自验 window 0：
   `cd FH && /Users/mark/sdk/mamba-envs/trading/bin/python3 run_wf_window.py --window 0`。
   262 800 bars 约为全历史的 1/9，预期 1-3 min；>5 min 未出结果再排查，不要提前 kill。
4. 若 BOUNDARY MISMATCH 退出：说明上游数据/loader 变了，**不得修改 EXPECTED 表去迁就**，
   原样上报（这是该断言存在的意义）。

### 验收（可运行命令，全部须通过）

```bash
cd /Users/mark/multica/quant-loop/research/swarm/2026-07-25/signal-enhance-h3/full_history && \
/Users/mark/sdk/mamba-envs/trading/bin/python3 - <<'EOF'
import json, math
w = json.load(open("results/se_h3_wf_window_0.json"))
assert w["window_id"] == 0
assert w["test_start_iso"] == "2022-11-20 16:01:00", w["test_start_iso"]
assert w["test_end_iso"] == "2023-05-22 04:00:00", w["test_end_iso"]
assert w["test_bars"] == [525600, 788400], w["test_bars"]
assert w["n_trades"] >= 0 and math.isfinite(w["sharpe_daily_resampled"])
print("T08 ACCEPT OK  window0 sharpe =", round(w["sharpe_daily_resampled"], 3),
      " n_trades =", w["n_trades"])
EOF
```

另跑一条防漂移不变量（对任意 K 适用，这里验 window 0 重跑幂等）：
`cd FH && /Users/mark/sdk/mamba-envs/trading/bin/python3 run_wf_window.py --window 0` 二次运行 exit 0，
且除 window 0 两个产物外 `results/` 下其他 `se_h3_wf_*` 文件的 mtime 不变。

### 依赖

- 前置: T02、T03、T04（模块契约），T06 绿（质量闸门）。与 T07 互不依赖，可并行。
- 被依赖: T09-T14（复用本脚本跑 window 1-6），T15（读 `se_h3_wf_window_*.json`）。

---

## 跨片冲突/注意事项（给 orchestrator）

1. **machine 分配**: round-1 wave-3 建议 T07/T08 分 Mac/server-105 各一，但 105 是否有
   `quant-loop/data/` parquet 树与 `full_history/` 上游模块未经本 slice 验证；两张卡默认都派 mac，
   派 105 前必须先核实数据与 `FH/` 同步。
2. **CPU 争用**: T07（10-25 min 单线程）若与 T08 同机并行可接受（各单核），但不要再叠 T09-T14
   或 H3-execution-maker 的长循环（单机同时 ≤4 个 Python 长循环，耗时本身是证据）。
3. **冻结依赖**: 两张卡的验收依赖以下文件字节级稳定——
   `run_btcsol_variants_fixed.py`、`mtf_xs_pairs_base_20260718.py`、H3 `config.json`、
   `data/perp_1m/*`、`data/funding/*`、`H3-baseline-repro/metrics.json`。
   任何清理/重构/数据刷新的 workstream 在 sprint 期间动这些文件会让 T07 指标漂移、
   T08 边界断言假阴性失败。
4. T08 的 EXPECTED 表是判决级锚点：只许在「数据层确实变更且主线知情」时由主线更新，
   执行 agent 无权修改。
