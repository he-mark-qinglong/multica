# w4-s5 — W4 T09-T15 执行卡（window 1-6 并行 + 聚合证据包）

- 片: `w4-s5`（细化 round1 `w4-signal-enhance-h3.md` 的 T09-T15）
- 日期: 2026-07-25
- 范围: `quant-loop/research/swarm/2026-07-25/signal-enhance-h3/full_history/`（下文 `FH`）+
  server-105 上镜像路径 `/home/smark/multica/quant-loop/research/swarm/2026-07-25/signal-enhance-h3/full_history/`（下文 `FH105`）
- 通用纪律: 不改任何生产/共享代码；不做 git 操作；windows 3+3 分摊 Mac/.105，单机同时 ≤4 个重循环；
  G5(CPCV)/G7(DSR) 本 workstream 不跑，聚合器必须如实标 NOT_RUN，不得伪造字段。

---

## 0. 环境事实（2026-07-25 round-2 实测，卡片自包含依据）

### 0.1 两台机器

| | Mac（本机） | server-105（Linux, 16C/23GB） |
|---|---|---|
| repo | `/Users/mark/multica` | `/home/smark/multica`（HEAD `e3a8e655f`，**落后且有未提交改动**） |
| python | `/Users/mark/sdk/mamba-envs/trading/bin/python3`（pandas 2.2.3 / numpy 1.26.4 / pyarrow 18.1.0） | `/usr/bin/python3`（pandas **3.0.3** / numpy 2.4.6 / matplotlib 3.11.0 / scipy 1.18.0，可用） |
| ssh | — | `ssh smark@192.168.0.105`（BatchMode 免密已验证） |
| 数据 | `quant-loop/data/perp_1m/{BTC,SOL}USDT_1m.parquet`、`data/funding/{BTC,SOL}USDT.parquet` | 同相对路径，**4 个文件字节数与 Mac 完全一致**（已核对 stat） |

⚠️ **pandas 版本不一致（2.2.3 vs 3.0.3）是已知的跨机数值漂移风险**。缓解：(a) 每个窗口 json 的
`test_start_iso` 断言（run_wf_window.py 内置，见 T08）能抓住索引错位；(b) T09S 在 .105 上断言
对齐索引长度 == 2448219；(c) 每个 .105 窗口卡额外落一个 `*.env.txt` 记录版本，T15 在 VERDICT.md
里如实写明窗口是混合环境算出的。

### 0.2 .105 缺失的文件（T09S 要同步的）

实测 .105 **没有**: `quant-loop/strategies/_indicators/mtf_xs_pairs_base_20260718.py`、
`quant-loop/strategies/mtf_xs_pairs_1m_15m_2h_h3_20260718/config.json`、整个
`quant-loop/research/swarm/2026-07-25/` 树。
实测 .105 **已有**: `quant-loop/_shared/gates/enforce.py`、`quant-loop/_shared/validation/{compute_metrics.py,cpcv.py}`、数据 parquet。
为保证字节一致，T09S 仍会覆盖同步 `_shared` 的 3 个 py 文件。

### 0.3 基线锚点值（逐字摘自 `H3-baseline-repro/metrics.json`，验收断言用）

全历史: `n_bars=2448219`，span `2021-11-20 16:01:00` → `2026-07-17 19:39:00`，`n_trades=40963`，
`sharpe_daily_resampled=1.4683`，MDD `-0.1626`。
OOS 聚合: `oos_sharpe_mean=1.8748`，`bootstrap_ci=[0.8879, 2.9363]`（seed 42 / 10000 次），
`oos_ann_mean=0.3179`，`oos_worst_mdd=-0.1330`，`oos_pf_mean=1.0122`。
Fee shock Sharpe: 4bps `1.3683` / 24bps `0.8699` / 60bps `-0.0213`。
Baseline 门禁: 仅 G4 FAIL（PF 1.01 < 1.5）。

7 窗边界 + baseline Sharpe（window runner 的断言表与聚合对照表都用这组数）:

| win | test_start_iso | test_end_iso | baseline n_trades | baseline Sharpe |
|---|---|---|---|---|
| 0 | 2022-11-20 16:01:00 | 2023-05-22 04:00:00 | 4434 | 1.725 |
| 1 | 2023-05-22 04:01:00 | 2023-11-20 16:00:00 | 4543 | 1.596 |
| 2 | 2023-11-20 16:01:00 | 2024-05-21 04:00:00 | 4182 | 1.047 |
| 3 | 2024-05-21 04:01:00 | 2024-11-19 16:00:00 | 4317 | 3.077 |
| 4 | 2024-11-19 16:01:00 | 2025-05-21 04:00:00 | 4341 | 1.688 |
| 5 | 2025-05-21 04:01:00 | 2025-11-19 16:00:00 | 4194 | -0.380 |
| 6 | 2025-11-19 16:01:00 | 2026-05-21 04:00:00 | 4321 | 4.369 |

窗口几何（来自 H3 config `walk_forward`）: train 525600 / test 262800 / step 262800，expanding-train，
**信号只在 test 切片内建**。

### 0.4 上游任务交付契约（T01-T08，由别的片细化；本片的依赖接口）

- T02 `FH/se_h3_common.py`: 提供 `load_aligned_data()` → `(d1m, funding, common_idx)`，
  `common_idx` 长度必须 == 2448219。
- T04 `FH/se_h3_loop.py`: 提供 `run_se_h3(d1m, cfg, funding)`（信号+循环+组合，锁定参数
  slope_lookback=4 favorable / adverse_stop_z=0.7 / regime_break=9.0 / fee 1+1bps）。
- T07 产物: `FH/results/se_h3_full_history_metrics.json`、`se_h3_trades.csv`、`se_h3_equity_daily.csv`、
  `se_h3_fee_shock.json`（键: `inhouse_4bps_rt`/`freqtrade_24bps_rt`/`backtrader_60bps_rt`，
  各含 `sharpe_daily_resampled` 等）。**T07 跑在哪台机器由 wave-3 的卡决定，T15 必须两边都能拉。**
- T08 `FH/run_wf_window.py`: `--window K` 参数；内置 §0.3 边界表断言（不符 exit 1）；
  产物 `FH/results/se_h3_wf_window_{K}.json`（含 `window_id/test_start_iso/test_end_iso/n_trades/
  sharpe_daily_resampled/annualized_return_daily_resampled/max_drawdown_pct/profit_factor`）
  + `se_h3_wf_trades_{K}.csv`。T08 已自验 window 0。
- T01 `FH/SPEC_signal_enhance_h3_fullhist.md`: 证伪条件 = OOS Sharpe < 1.0 **或** CI lower < 0.5
  **或** 60bps fee-shock Sharpe ≤ 0 → KILL 证据成立。

---

## T09S — .105 环境同步 + 预检（新增卡，.105 窗口的前置）

- **目标**: 把 .105 缺的只读依赖和 FH 代码同步过去，并证明 .105 能加载权威数据且索引与 Mac 一致。
- **机器**: mac（从 Mac 发起 rsync/ssh）。 **估时**: 10 min。
- **deps**: T08（需要 FH 下全部代码就位：se_h3_common/se_h3_signals/se_h3_loop/run_wf_window.py）。
- **读**: Mac 侧 `quant-loop/strategies/_indicators/mtf_xs_pairs_base_20260718.py`、
  `quant-loop/strategies/mtf_xs_pairs_1m_15m_2h_h3_20260718/config.json`、
  `quant-loop/research/swarm/2026-07-25/H3-variants-h1h2h4/run_btcsol_variants_fixed.py`、
  `quant-loop/research/swarm/2026-07-25/H3-baseline-repro/metrics.json`、
  `quant-loop/_shared/gates/enforce.py`、`quant-loop/_shared/validation/{compute_metrics.py,cpcv.py}`、
  `FH/` 整目录。
- **写**: 仅 .105 侧镜像路径（rsync 目标），Mac 侧零写入（日志可直接打印不落盘）。

步骤:

1. 同步代码（在 Mac 上执行；`-R` + `./` 锚点保持相对路径）:

```bash
cd /Users/mark/multica
rsync -avzR \
  quant-loop/./strategies/_indicators/mtf_xs_pairs_base_20260718.py \
  quant-loop/./strategies/mtf_xs_pairs_1m_15m_2h_h3_20260718/config.json \
  quant-loop/./research/swarm/2026-07-25/H3-variants-h1h2h4/run_btcsol_variants_fixed.py \
  quant-loop/./research/swarm/2026-07-25/H3-baseline-repro/metrics.json \
  quant-loop/./_shared/gates/enforce.py \
  quant-loop/./_shared/validation/compute_metrics.py \
  quant-loop/./_shared/validation/cpcv.py \
  smark@192.168.0.105:/home/smark/multica/
rsync -avz \
  /Users/mark/multica/quant-loop/research/swarm/2026-07-25/signal-enhance-h3/full_history/ \
  smark@192.168.0.105:/home/smark/multica/quant-loop/research/swarm/2026-07-25/signal-enhance-h3/full_history/
```

2. 数据尺寸 sanity（两端 stat 对比，四个 parquet 字节数必须相等；不等则 STOP 并上报，不得继续）:

```bash
ssh smark@192.168.0.105 'stat -c "%s %n" /home/smark/multica/quant-loop/data/perp_1m/{BTC,SOL}USDT_1m.parquet /home/smark/multica/quant-loop/data/funding/{BTC,SOL}USDT.parquet'
# 期望: 213093031 BTCUSDT_1m / 143610060 SOLUSDT_1m / 101267 BTC funding / 103389 SOL funding
```

3. .105 导入 + 对齐索引预检:

```bash
ssh smark@192.168.0.105 'cd /home/smark/multica/quant-loop/research/swarm/2026-07-25/signal-enhance-h3/full_history && /usr/bin/python3 -c "
import se_h3_common as c
d, f, i = c.load_aligned_data()
assert len(i) == 2448219, len(i)
print(i[0], i[-1])
"'
# 期望输出首尾: 2021-11-20 16:01:00  2026-07-17 19:39:00
```

4. 记录 .105 环境版本（T15 证据用，直接打印收集即可，或落 `FH/results/env_105.txt` 后再 rsync 回 Mac——二选一，推荐后者）:

```bash
ssh smark@192.168.0.105 '/usr/bin/python3 -c "import sys, pandas, numpy; print(sys.version.split()[0], pandas.__version__, numpy.__version__)"; hostname'
```

- **验收**: 步骤 3 命令 exit 0，输出 `2448219` 行断言通过且首尾时间戳为
  `2021-11-20 16:01:00` / `2026-07-17 19:39:00`；步骤 2 四个字节数全等。
- **失败处理**: 任一步失败 → 不要降级到「6 窗全在 Mac 跑」（违反单机 ≤4 重循环纪律）；
  上报阻塞原因，T12-T14 标记 BLOCKED。

---

## T09 — window 1 执行（Mac）

- **目标**: 跑 walk-forward window 1（test 2023-05-22 04:01 → 2023-11-20 16:00），落窗口证据。
- **机器**: mac。 **估时**: 10 min（单窗运行 <5 min + 校验）。
- **deps**: T08。
- **读**: `FH/run_wf_window.py`（T08 写的，只执行不修改）、`FH/se_h3_*.py`、
  `quant-loop/data/perp_1m/`、`quant-loop/data/funding/`。
- **写**: `FH/results/se_h3_wf_window_1.json`、`FH/results/se_h3_wf_trades_1.csv`（若已存在且验收通过则跳过运行，直接验收——幂等）。

步骤:

1. `cd /Users/mark/multica/quant-loop/research/swarm/2026-07-25/signal-enhance-h3/full_history`
2. 运行: `/Users/mark/sdk/mamba-envs/trading/bin/python3 run_wf_window.py --window 1`
   （runner 内置 §0.3 边界表断言；边界不符会 exit 1，那是数据漂移信号，STOP 上报，不要改断言）。
3. 验收（见下）。
4. 在结果汇报里记录: 运行耗时、`n_trades`、`sharpe_daily_resampled`，并与 baseline 窗 1
   Sharpe 1.596 并列列出（仅对照，不作门槛）。

- **验收**:

```bash
cd /Users/mark/multica/quant-loop/research/swarm/2026-07-25/signal-enhance-h3/full_history
/Users/mark/sdk/mamba-envs/trading/bin/python3 -c "
import json, math
m = json.load(open('results/se_h3_wf_window_1.json'))
assert m['test_start_iso'] == '2023-05-22 04:01:00', m['test_start_iso']
assert m['test_end_iso'] == '2023-11-20 16:00:00', m['test_end_iso']
assert m['n_trades'] >= 0 and math.isfinite(m['sharpe_daily_resampled'])
print('OK', m['n_trades'], m['sharpe_daily_resampled'])
"
```

---

## T10 — window 2 执行（Mac）

- **目标**: 跑 window 2（test 2023-11-20 16:01 → 2024-05-21 04:00）。
- **机器**: mac。 **估时**: 10 min。
- **deps**: T08。
- **读/写**: 同 T09，K=2（`se_h3_wf_window_2.json` / `se_h3_wf_trades_2.csv`）。
- **步骤**: 同 T09，`--window 2`；对照 baseline 窗 2 Sharpe 1.047。
- **验收**: 同 T09 的命令，改 K=2，期望
  `test_start_iso == '2023-11-20 16:01:00'`、`test_end_iso == '2024-05-21 04:00:00'`。

---

## T11 — window 3 执行（Mac）

- **目标**: 跑 window 3（test 2024-05-21 04:01 → 2024-11-19 16:00）。
- **机器**: mac（故意放 Mac：此窗与 quick_verify 的 2024 自然年切片重叠最高，是直观对照点，
  与 T05/T06 的 parity 产物同机便于人工核对；**不作验收门槛**）。 **估时**: 10 min。
- **deps**: T08。
- **读/写**: 同 T09，K=3。
- **步骤**: 同 T09，`--window 3`；对照 baseline 窗 3 Sharpe 3.077。
- **验收**: 同 T09 的命令，改 K=3，期望
  `test_start_iso == '2024-05-21 04:01:00'`、`test_end_iso == '2024-11-19 16:00:00'`。

---

## T12 — window 4 执行（server-105）

- **目标**: 在 .105 上跑 window 4（test 2024-11-19 16:01 → 2025-05-21 04:00）。
- **机器**: 105（agent 直接在 .105 上工作，repo 根 `/home/smark/multica`）。 **估时**: 10 min。
- **deps**: T09S。
- **读**: `FH105/run_wf_window.py`、`FH105/se_h3_*.py`、`.105:quant-loop/data/{perp_1m,funding}/`。
- **写**: `FH105/results/se_h3_wf_window_4.json`、`FH105/results/se_h3_wf_trades_4.csv`、
  `FH105/results/se_h3_wf_window_4.env.txt`。**不要** scp 回 Mac（T15 统一拉取）。

步骤:

1. `cd /home/smark/multica/quant-loop/research/swarm/2026-07-25/signal-enhance-h3/full_history`
2. 落环境证据（先于运行，证明混合环境的可溯源性）:

```bash
/usr/bin/python3 -c "
import sys, pandas, numpy, socket
open('results/se_h3_wf_window_4.env.txt', 'w').write(
    f'host {socket.gethostname()}\npython {sys.version.split()[0]}\npandas {pandas.__version__}\nnumpy {numpy.__version__}\n')
"
```

3. 运行: `/usr/bin/python3 run_wf_window.py --window 4`
4. 验收（见下）。
5. 结果汇报记录: 耗时、`n_trades`、`sharpe_daily_resampled`、pandas 版本；对照 baseline 窗 4 Sharpe 1.688。

- **验收**:

```bash
cd /home/smark/multica/quant-loop/research/swarm/2026-07-25/signal-enhance-h3/full_history
/usr/bin/python3 -c "
import json, math
m = json.load(open('results/se_h3_wf_window_4.json'))
assert m['test_start_iso'] == '2024-11-19 16:01:00', m['test_start_iso']
assert m['test_end_iso'] == '2025-05-21 04:00:00', m['test_end_iso']
assert m['n_trades'] >= 0 and math.isfinite(m['sharpe_daily_resampled'])
print('OK', m['n_trades'], m['sharpe_daily_resampled'])
"
```

---

## T13 — window 5 执行（server-105）

- **目标**: 在 .105 上跑 window 5（test 2025-05-21 04:01 → 2025-11-19 16:00）。
  注意: 这是 baseline 唯一负 Sharpe 的窗（-0.380），增强版在此窗的表现是 SPEC 证伪判读的关键证据。
- **机器**: 105。 **估时**: 10 min。
- **deps**: T09S。
- **读/写**: 同 T12，K=5（含 `se_h3_wf_window_5.env.txt`）。
- **步骤**: 同 T12，`--window 5`；对照 baseline 窗 5 Sharpe -0.380。
- **验收**: 同 T12 的命令，改 K=5，期望
  `test_start_iso == '2025-05-21 04:01:00'`、`test_end_iso == '2025-11-19 16:00:00'`。

---

## T14 — window 6 执行（server-105）

- **目标**: 在 .105 上跑 window 6（test 2025-11-19 16:01 → 2026-05-21 04:00）。
- **机器**: 105。 **估时**: 10 min。
- **deps**: T09S。
- **读/写**: 同 T12，K=6（含 `se_h3_wf_window_6.env.txt`）。
- **步骤**: 同 T12，`--window 6`；对照 baseline 窗 6 Sharpe 4.369。
- **验收**: 同 T12 的命令，改 K=6，期望
  `test_start_iso == '2025-11-19 16:01:00'`、`test_end_iso == '2026-05-21 04:00:00'`。

---

## T15 — 聚合 + 门禁映射 + VERDICT.md 证据包（Mac）

- **目标**: 合并 7 窗 + 全历史 + fee shock，跑 `certify_metrics`（G5/G7 如实 NOT_RUN），
  产出判决证据包。**不写 KEEP/KILL 结论**（判决归研究主线）。
- **机器**: mac。 **估时**: 20 min。
- **deps**: T07, T09, T10, T11, T12, T13, T14（window 0 由 T08 自验产物提供）。
- **读**:
  - `FH/results/se_h3_wf_window_{0..6}.json`（0-3 本地；4-6 步骤 0 从 .105 拉）
  - `FH/results/se_h3_full_history_metrics.json`、`FH/results/se_h3_fee_shock.json`（T07）
  - `FH/SPEC_signal_enhance_h3_fullhist.md`（T01，证伪条件原文）
  - `quant-loop/research/swarm/2026-07-25/H3-baseline-repro/metrics.json`（对照锚点）
  - `quant-loop/_shared/gates/enforce.py`（`certify_metrics`，只读 import）
- **写**: `FH/aggregate_verdict.py`（新建）、`FH/results/se_h3_metrics.json`、`FH/VERDICT.md`。

步骤:

0. **收集产物**（在 Mac 上；幂等——已存在则跳过）:

```bash
FH=/Users/mark/multica/quant-loop/research/swarm/2026-07-25/signal-enhance-h3/full_history
FH105=/home/smark/multica/quant-loop/research/swarm/2026-07-25/signal-enhance-h3/full_history
for K in 4 5 6; do
  for f in se_h3_wf_window_$K.json se_h3_wf_trades_$K.csv se_h3_wf_window_$K.env.txt; do
    [ -f "$FH/results/$f" ] || scp -q smark@192.168.0.105:"$FH105/results/$f" "$FH/results/$f"
  done
done
# T07 产物若本地缺（T07 可能跑在 .105），同样拉:
for f in se_h3_full_history_metrics.json se_h3_fee_shock.json se_h3_trades.csv se_h3_equity_daily.csv; do
  [ -f "$FH/results/$f" ] || scp -q smark@192.168.0.105:"$FH105/results/$f" "$FH/results/$f"
done
ls -la "$FH/results/"   # 必须齐: window 0-6 json + 全历史 metrics + fee_shock
```

若任一 window json 拉不到 → STOP，把缺的窗口号上报，不得用部分窗口出聚合。

1. **写 `FH/aggregate_verdict.py`**。结构（关键逻辑照抄权威实现，标注来源行号）:

```python
"""Aggregate se_h3 7-window WF + full-history + fee shock into verdict evidence."""
import json, sys
from pathlib import Path
import numpy as np

FH = Path(__file__).resolve().parent
RES = FH / "results"
QL = FH.parents[3]                       # quant-loop root
sys.path.insert(0, str(QL / "_shared" / "gates"))
from gates.enforce import certify_metrics  # 或 from enforce import certify_metrics（按 T02 的 path 装配方式）

BASELINE = json.load(open(QL / "research/swarm/2026-07-25/H3-baseline-repro/metrics.json"))

# --- 1) 7 windows ---
per_window = []
for k in range(7):
    w = json.load(open(RES / f"se_h3_wf_window_{k}.json"))
    per_window.append(w)
sharpes = np.array([w["sharpe_daily_resampled"] for w in per_window])
anns    = np.array([w["annualized_return_daily_resampled"] for w in per_window])
mdds    = np.array([w["max_drawdown_pct"] for w in per_window])
pfs     = np.array([w["profit_factor"] for w in per_window])
n_trades_total = int(sum(w["n_trades"] for w in per_window))

# bootstrap CI — verbatim from run_btcsol_variants_fixed.py L293-299
rng = np.random.default_rng(42)
boot = np.empty(10000)
for k in range(10000):
    boot[k] = sharpes[rng.integers(0, len(sharpes), size=len(sharpes))].mean()
ci_lo, ci_hi = float(np.percentile(boot, 2.5)), float(np.percentile(boot, 97.5))

oos = {
    "n_windows": 7,
    "per_window": per_window,
    "oos_sharpe_mean_daily_resampled": float(np.mean(sharpes)),
    "oos_annualized_mean_daily": float(np.mean(anns)),
    "oos_max_drawdown_worst_pct": float(np.min(mdds)),
    "oos_profit_factor_mean": float(np.mean(np.where(np.isfinite(pfs), pfs, 0.0))),
    "bootstrap_ci_lower": ci_lo,
    "bootstrap_ci_upper": ci_hi,
    "n_trades_total": n_trades_total,
}

# --- 2) full history + fee shock (T07 artifacts) ---
full = json.load(open(RES / "se_h3_full_history_metrics.json"))
fee  = json.load(open(RES / "se_h3_fee_shock.json"))

# --- 3) gates mapping; G5/G7 deliberately absent -> MISSING_FIELD -> relabel NOT_RUN ---
gate_input = {
    "sharpe_daily": oos["oos_sharpe_mean_daily_resampled"],          # G1
    "annualized_return": oos["oos_annualized_mean_daily"],           # G2
    "max_drawdown_pct": oos["oos_max_drawdown_worst_pct"],           # G3
    "profit_factor": oos["oos_profit_factor_mean"],                  # G4
    "bootstrap_ci95_lower": oos["bootstrap_ci_lower"],               # G6
    "n_trades": n_trades_total,                                      # T1
    # 不提供 cpcv_mean_oos_sharpe (G5) / deflated_sharpe (G7) —— 本 workstream 不跑
}
res = certify_metrics(gate_input)
gate_status = {g: ("FAIL" if g in res.failed_gates else "PASS")
               for g in ["G1","G2","G3","G4","G5","G6","G7","T1"]}
gate_status["G5"] = "NOT_RUN"   # CPCV 双框架 CV 留后续 workstream
gate_status["G7"] = "NOT_RUN"   # DSR 留后续 workstream
# res.reasons 原文保留进 metrics json 作 provenance（应含 G5/G7 的 MISSING_FIELD 记录）

out = {
    "strategy": "signal-enhance-h3 full-history validation",
    "params": {"slope_lookback": 4, "slope_sign": "favorable",
               "adverse_stop_z": 0.7, "regime_break": 9.0,
               "z_entry": 2.5, "z_exit": 0.5, "max_hold": 240,
               "fee_bps_per_side": 1.0, "slip_bps_per_side": 1.0},
    "oos": oos,
    "full_history": full,
    "fee_sensitivity": fee,
    "gates": {"status": gate_status, "raw_reasons": res.reasons,
              "note": "G5/G7 NOT_RUN by design; MISSING_FIELD FAILs relabeled, not hidden"},
    "baseline_reference": BASELINE["walk_forward_oos"] | {"full_history": BASELINE["full_history"]},
    "environment": {"windows_0_3": "mac pandas 2.2.3", "windows_4_6": "server-105 pandas 3.0.3"},
}
json.dump(out, open(RES / "se_h3_metrics.json", "w"), indent=2)
```

（`from gates.enforce import ...` 还是 `from enforce import ...` 取决于 sys.path 插到哪一级；
T02 的 se_h3_common 已有现成装配，直接 `from se_h3_common import ...` 复用或照抄它的 path 段。）

2. **写 `FH/VERDICT.md`**（聚合脚本生成或手写均可，内容必须含）:

   - 标题 + 日期 + 一句话证据摘要（例如「增强版 OOS Sharpe X.XX vs baseline 1.87，
     60bps Sharpe X.XX，证伪条件 N/3 触发」），**结尾明确写「KEEP/KILL 判决留研究主线」**。
   - 逐窗对照表: 7 行，每行 `win | test_start | se_h3 Sharpe | baseline Sharpe | se_h3 n_trades | baseline n_trades | se_h3 MDD`，
     baseline 列用 §0.3 的锚点值。
   - 聚合对照表: OOS mean Sharpe / CI lower / worst MDD / PF mean / ann（se_h3 vs baseline §0.3 锚点）。
   - Fee shock 表: 4/24/60bps 三行（se_h3 vs baseline 1.3683 / 0.8699 / -0.0213）。
   - SPEC 证伪条件逐条判定（条件原文引自 `FH/SPEC_signal_enhance_h3_fullhist.md`，预期为
     OOS Sharpe<1.0 / CI lower<0.5 / 60bps Sharpe≤0 三条，每条给 TRUE/FALSE + 实际数值）。
   - 门禁结果表: G1-G4/G6/T1 实际 PASS/FAIL + 数值；**G5/G7 明确标 NOT_RUN（原因: CPCV/DSR
     属后续 workstream），并附 `certify_metrics` 原始 reasons**。
   - 环境说明: window 0-3 在 Mac（pandas 2.2.3）、window 4-6 在 .105（pandas 3.0.3）算出，
     边界断言全部通过；如 G4 FAIL（大概率，2024 子样本 PF 仅 1.087）如实写 FAIL，不粉饰。

3. 运行 + 验收。

- **验收**:

```bash
cd /Users/mark/multica/quant-loop/research/swarm/2026-07-25/signal-enhance-h3/full_history
/Users/mark/sdk/mamba-envs/trading/bin/python3 aggregate_verdict.py && \
/Users/mark/sdk/mamba-envs/trading/bin/python3 -c "
import json
m = json.load(open('results/se_h3_metrics.json'))
assert m['oos']['n_windows'] == 7
assert 'bootstrap_ci_lower' in m['oos']
assert 'fee_sensitivity' in m
assert m['gates']['status']['G5'] == 'NOT_RUN' and m['gates']['status']['G7'] == 'NOT_RUN'
print('OK', m['oos']['oos_sharpe_mean_daily_resampled'], m['oos']['bootstrap_ci_lower'])
" && grep -c "NOT_RUN" VERDICT.md   # 期望 >= 2
```

  另: VERDICT.md 必须含 7 窗逐窗 Sharpe 表 —— `grep -c "2025-11-19 16:01:00" VERDICT.md` ≥ 1 且
  表中 7 个窗口齐全（人工目检）。

---

## 依赖与波次（本片内）

```
T08(他片) ──► T09S ──► T12,T13,T14 (.105)
T08(他片) ──────────► T09,T10,T11 (mac)
T07(他片) + T09-T14 ──► T15 (mac)
```

- T09/T10/T11 与 T12/T13/T14 可六路并行（两机各 3，满足单机 ≤4 重循环）。
- T09S 必须先于 .105 三窗完成；Mac 三窗不依赖 T09S。

## 跨片冲突 / 风险（供编排者）

1. **T15 依赖 T07 产物但 T07 的机器归属在别的片**：本卡步骤 0 已做双机拉取兜底；若 T07 卡把产物
   写到别的路径，T15 会 STOP 并报缺文件。
2. **.105 repo  stale（HEAD e3a8e655f，有未提交改动）**：T09S 用 rsync 覆盖指定文件而不是 git pull，
   不碰 .105 工作区的未提交改动；若别的片也在往 .105 rsync `_shared/`（如 w1 引擎片），
   wave-4 期间请冻结 `_shared/gates/enforce.py`、`_shared/validation/{compute_metrics.py,cpcv.py}` 三文件。
3. **pandas 2.2.3 (mac) vs 3.0.3 (.105)**：混合环境窗口聚合是既定取舍（round1 §5.3 的分机要求），
   风险通过边界断言 + env.txt 溯源 + VERDICT 如实披露来控制；若主线要求严格同环境，
   备选方案是 6 窗全 Mac 分两波跑（每波 3，约 +15 min），需主线决策，本片不擅自改。
4. 冻结清单重申（round1 §5.1）: `mtf_xs_pairs_base_20260718.py`、H3 `config.json`、
   `run_btcsol_variants_fixed.py`、`data/perp_1m/*.parquet`、`data/funding/*.parquet`、
   `H3-baseline-repro/metrics.json` 在 sprint 期间任何片都不得改。
