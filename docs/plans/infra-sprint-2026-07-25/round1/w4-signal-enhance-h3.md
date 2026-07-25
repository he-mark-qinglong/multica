# W4 — signal-enhance-h3 全历史验证 harness（round-1 计划）

- slug: `w4-signal-enhance-h3`
- 日期: 2026-07-25
- 范围: `quant-loop/research/swarm/2026-07-25/signal-enhance-h3/`（只新增，不改既有文件）
- 目标: 把 2024 子样本验证过的组合过滤（15m z-slope 顺向过滤 lookback=4 + 0.7z 逆势止损 + regime_break=9.0）
  接到与 H3 baseline 逐位一致的权威管线（`H3-variants-h1h2h4/run_btcsol_variants_fixed.py` 那套），
  跑 2021-11→2026-07 全历史 + 7 窗 walk-forward OOS + 4/24/60bps fee shock，产出证据包供主线判决。
- 纪律: 这是「已定型 SPEC 的执行验证」，符合 §3.1 swarm 使用边界；KEEP/KILL 判决本身回主线单线程，不在本 workstream。

---

## 1. Current-state findings（全部经实际读码验证）

### 1.1 权威管线 = `run_btcsol_variants_fixed.py`，与 baseline 逐位一致（已证）

`quant-loop/research/swarm/2026-07-25/H3-variants-h1h2h4/run_btcsol_variants_fixed.py`：

- 数据加载器 L90-128：`data/perp_1m/{BTC,SOL}USDT_1m.parquet` + `data/funding/{BTC,SOL}USDT.parquet`（`ts` 列），
  对齐公共索引并裁到 funding 可得区间（L112-128）。
- 成本模型 L79-84：fee 1bps + slip 1bps per side per leg（= 4bps pair RT），**成本只记 trade log，equity 为 gross**。
- 指标 L220-239：`compute_metrics` + `sharpe_daily_resampled` + `profit_factor_and_mdd`（daily 法）。
- Walk-forward L242-310：expanding-train、**只在 test slice 上建信号**（L263 `df.iloc[te_s:te_e]` → `run_backtest`），
  bootstrap seed 42 / 10000 次（L293-299）。
- Fee shock L313-346：对 gross daily equity 按 exit 日扣 `(rt_bps/1e4)*0.005` per trade，跑 4/24/60 bps（L419-421）。
- **逐位一致性已证**：`run_fixed.log` 尾部 sanity — `delta_oos_sharpe: 0.0`、`delta_bootstrap_ci_lower: -1.1e-16`、
  `n_trades 40963 == 40963`（vs `H3-baseline-repro/metrics.json`）。

### 1.2 base 引擎的挂载点（生产代码，只读）

`quant-loop/strategies/_indicators/mtf_xs_pairs_base_20260718.py`（854 行）：

- `build_h3_signals` L318-381：z(lookback 240)、fund_allow（2h funding EMA < 5e-4）、15m ATR `size_scale`。
  **注意 L376-379：params 只有 z_entry/z_exit/max_hold，没有 regime_break** → `_backtest_pair` L473 取默认 3.0。
- `_backtest_pair` L463-605：entry L494-554（fund_allow 检查 L522-525）；exit 链 L562-568 是 if/elif：
  `z_mean_revert → regime_break → max_holding`；成本 L576 `2*2*(fee+slip)/1e4` 记 `pnl_pct`(net)。
- H1 的 slope 检查（L503-506）是 **adverse 约定**（direction=+1 要求 slope<0）——与 signal-enhance 的
  **favorable 约定相反**，所以不能简单往 signals 里塞 `z_slope_15m` 复用，必须复制循环改造。
- `run_backtest` L809-855 按 `cfg["hypothesis"]` 分发；H3 sizing 走 `size_scale`（L845-846）。

### 1.3 待移植的增强逻辑（2024 子样本证据）

`signal-enhance-h3/run_experiments.py`：

- `enhance_signals` L54-91：`z_slope_4` = pair z 聚合到 15min（L71）→ `zscore_slope(z_15m, 4)`（L72）→
  `align_lower_to_upper` 回 1m。全部用 base 模块原语，可原位复用。
- `backtest_variant` L115-280：favorable 过滤 L182-190（direction=+1 要求 slope>0，=-1 要求 slope<0）；
  adverse stop L233-237（`|z_entry - z_current| ≥ 0.7`，exit_reason="adverse_stop"）；
  exit 顺序 `z_mean_revert → regime_break → adverse_stop → max_holding`（L229-239）。
- `quick_verify.py` L42：候选参数 = `slope_filter{lookback:4, sign:favorable} + adverse_stop_z:0.7 + regime_break:9.0`
  （regime_break 抬到 9.0 等效禁用宽止损）。
- 2024 结果（`quick_verify.log` / `quick_verify_2024.json`）：组合变体 **704 trades, +15.43bps net,
  win 68.9%, Sharpe 8.0735, PF 1.087, MDD -3.15%**。

### 1.4 ⚠️ 关键发现：2024 证据用的 funding 数据不是权威源

`signal-enhance-h3/data_loader_patch.py` L19-20：BTC funding 读 `funding_analysis/BTCUSDT_funding.parquet`，
SOL funding 读 `strategies/_graveyard/xs_pairs_30m/.../SOLUSDT__funding.parquet` —— 与权威管线
`data/funding/{BTC,SOL}USDT.parquet`（fixed runner L59-60）**不同源**。fund_allow 过滤可能有差异，
所以 8.07 这个数不能直接外推；全历史必须用权威 loader 重跑（这正是本 workstream 的意义）。
1m klines 两边同源（`data/perp_1m/`，L18 vs fixed runner L59），仅 funding 不同。

### 1.5 baseline 锚点值（parity/回归检查用）

`H3-baseline-repro/metrics.json`：n_bars=2 448 219，span 2021-11-20 16:01 → 2026-07-17 19:39；
full n_trades=40 963；OOS Sharpe 1.8748 / CI lower 0.8879；60bps fee-shock Sharpe -0.021。
7 窗边界（`walk_forward_oos.per_window`，train 525600 / test 262800 / step 262800，见 config L44-48）：

| win | test_start | test_end | baseline Sharpe |
|---|---|---|---|
| 0 | 2022-11-20 16:01 | 2023-05-22 04:00 | 1.725 |
| 1 | 2023-05-22 04:01 | 2023-11-20 16:00 | 1.596 |
| 2 | 2023-11-20 16:01 | 2024-05-21 04:00 | 1.047 |
| 3 | 2024-05-21 04:01 | 2024-11-19 16:00 | 3.077 |
| 4 | 2024-11-19 16:01 | 2025-05-21 04:00 | 1.688 |
| 5 | 2025-05-21 04:01 | 2025-11-19 16:00 | -0.380 |
| 6 | 2025-11-19 16:01 | 2026-05-21 04:00 | 4.369 |

### 1.6 门禁模块

`_shared/gates/enforce.py` `certify_metrics` L82+：缺失必填字段 = 显式 FAIL（L96-101，P1 修复后语义）。
本 harness 不跑 G5(CPCV)/G7(DSR)，聚合器必须把它们如实标 NOT_RUN，不得伪造字段。
G4 (PF>1.5) 大概率 FAIL（2024 PF 仅 1.087）——如实报告，判决归主线。

### 1.7 落盘纪律

`run_experiments.py` L7-8 已确立先例：「All code lives in the swarm research directory; the production
strategy and shared modules are only imported read-only.」本 workstream 沿用：所有新文件放
`signal-enhance-h3/full_history/`，结果放 `full_history/results/`，不覆盖目录内任何既有产物。

---

## 2. 任务清单（15 个）

> 通用约束：python 一律 `/Users/mark/sdk/mamba-envs/trading/bin/python3`；所有新文件只在
> `quant-loop/research/swarm/2026-07-25/signal-enhance-h3/full_history/` 下；同一 parallel-group 内文件不相交。
> 下文 `FH` = 上述 full_history 目录。

### T01 — 预注册 SPEC（group A, S, deps: 无）

- 目标: 写死假设、参数、证伪条件，禁止事后调参。
- 文件: `FH/SPEC_signal_enhance_h3_fullhist.md`（新建）
- 内容要点: 假设一句话（15m 顺向转弯入场 + 0.7z 逆势止损过滤掉 78% regime_break 亏损交易）；
  参数锁定（z_entry 2.5, z_exit 0.5, max_hold 240, slope lookback 4 favorable, adverse_stop_z 0.7,
  regime_break 9.0, fee 1+1bps, BTC+SOL only）；证伪条件（OOS Sharpe < 1.0 或 CI lower < 0.5 或
  60bps Sharpe ≤ 0 → KILL 证据成立）；引用 §1.4 说明 2024 数不作证据。
- 验收: `grep -c "adverse_stop_z" FH/SPEC_signal_enhance_h3_fullhist.md` ≥1 且含「证伪」一节。

### T02 — 公共模块 `se_h3_common.py`（group A, S, deps: 无）

- 目标: sys.path 装配 + 从 fixed runner 原位 import 权威函数，杜绝复制漂移。
- 文件: `FH/se_h3_common.py`（新建）
- 内容: 把 `H3-variants-h1h2h4/` 加 sys.path，`from run_btcsol_variants_fixed import load_perp_1m,
  load_funding, align_and_clip, portfolio_metrics, fee_shock_metrics, load_config`；提供
  `load_aligned_data()`（返回 d1m/funding/common_idx）与 `load_se_h3_config()`（H3 config + 锁定覆盖参数，
  含 `slope_lookback=4, adverse_stop_z=0.7, regime_break=9.0` 常量定义）。
- 验收: `python3 -c "import se_h3_common as c; d,f,i=c.load_aligned_data(); assert len(i)==2448219, len(i); print(i[0], i[-1])"` 输出 2448219 行、span 与 §1.5 一致。

### T03 — 信号模块 `se_h3_signals.py`（group A, S, deps: 无）

- 目标: baseline H3 信号 + favorable slope 列，全部调用 base 模块原语。
- 文件: `FH/se_h3_signals.py`（新建）
- 内容: `build_se_h3_signals(d1m, cfg, funding)`：先 `build_h3_signals`，再按 run_experiments.py L71-72
  的方法加 `z_slope_fav_4`（pair z → `aggregate_ohlcv(...,"15min")` → `zscore_slope(z_15m, 4)` →
  `align_lower_to_upper`）。键名用 `z_slope_fav_4`，**不得用 `z_slope_15m`**（避免触发 base 引擎 L503
  的 adverse 检查）。
- 验收: 60 000 bar 切片冒烟：`python3 FH/smoke_signals.py`（本任务一并写）assert z_slope 与 z 索引一致、
  warmup 后非 NaN 比例 >0.9，运行 <2 min。

### T04 — 回测循环模块 `se_h3_loop.py`（group A, M, deps: 无）

- 目标: 复制 `_backtest_pair` 并加两个钩子，语义与 quick_verify 完全一致。
- 文件: `FH/se_h3_loop.py`（新建）
- 内容: `backtest_pair_se(signals, pair, cfg, sizing_scale, fee_bps, slip_bps)`——逐行复制 base
  `_backtest_pair`（mtf_xs_pairs_base_20260718.py L463-605），改动仅三处：
  (a) entry 在 fund_allow 之后加 favorable slope 过滤（direction=+1 要求 slope>0；=-1 要求 slope<0；
  slope 为 NaN 则拒入，同 run_experiments L187-190）；
  (b) exit 链在 regime_break 与 max_holding 之间插 adverse_stop（pos=+1 且 zi ≤ entry_z−0.7 等，
  同 L233-237），regime_break 从 cfg 读（锁定 9.0）；
  (c) trade dict 键同时含 `pnl_pct`(net)、`gross_pct`、`exit_ts`、`exit_reason`，兼容
  `portfolio_metrics`（fixed runner L224）与 `fee_shock_metrics`（L320）。
  另提供 `run_se_h3(d1m, cfg, funding)`（= build signals → loop → `build_portfolio`，镜像 base
  `run_backtest` L843-855 的 H3 路径）。
- 验收: `python3 -c "import se_h3_loop"` 无错 + 模块内 `__all__` 含两个函数。

### T05 — 信号 parity 测试（group B, M, deps: T02, T03）

- 目标: 证明新信号链与 quick_verify 信号链在重叠区间逐位一致，并量化 funding 源差异。
- 文件: `FH/test_signal_parity.py`（新建）
- 内容: 2024 切片（2024-01-01→2024-12-31）：(a) 用 T03 + 权威 loader 建信号；(b) 用
  `run_experiments.enhance_signals` + `data_loader_patch` 建信号；交集索引上 assert
  `z`、`size_scale`、`z_slope_4`/`z_slope_fav_4` allclose(1e-12)；`fund_allow` 计算不一致 bar 数并
  打印（**不 assert 为 0**——§1.4 已知不同源，>5% 才 fail）。
- 验收: `python3 FH/test_signal_parity.py` exit 0 且输出 fund_allow mismatch 百分比；运行 <2 min。

### T06 — 循环 parity 测试（group B, M, deps: T02, T03, T04）

- 目标: 双锚定——过滤关闭时 == base 引擎；过滤开启时 == quick_verify 2024 结果。
- 文件: `FH/test_loop_parity.py`（新建）
- 内容: (a) 2024 切片权威信号 + 过滤器全关（slope=None, adverse_stop=None, regime_break=3.0）→
  trades 与 base `_backtest_pair` 输出逐笔相等（entry/exit ts、pnl_pct 1e-15）；
  (b) 用 quick_verify 自己的数据路径（data_loader_patch + enhance_signals + backtest_variant，
  参数同 quick_verify.py L42）与新循环跑同一 2024 切片 → trades 数==704，
  逐笔 pnl allclose(1e-12)，daily Sharpe == 8.0735 ± 1e-3。
- 验收: `python3 FH/test_loop_parity.py` exit 0，打印两项 parity OK；运行 <2 min。

### T07 — 全历史回测 + fee shock（group C, L, deps: T06）

- 目标: 全历史（2 448 219 bars）跑组合候选 + 4/24/60bps fee shock。
- 文件: `FH/run_full_history.py`（新建）；产物 `FH/results/se_h3_full_history_metrics.json`、
  `se_h3_trades.csv`、`se_h3_equity_daily.csv`、`se_h3_fee_shock.json`
- 内容: `load_aligned_data` → `run_se_h3` → `portfolio_metrics`（fixed runner L220 原函数）→
  trades 落 csv → `fee_shock_metrics`（L313 原函数）跑 4/24/60 bps → json 落盘（含 config 快照 +
  data_span + n_bars）。打印每阶段耗时。
- 验收: `python3 FH/run_full_history.py` 完成后 `python3 -c "import json; m=json.load(open('FH/results/se_h3_full_history_metrics.json')); assert m['n_bars']==2448219 and m['full_history']['n_trades']>0"`；
  fee_shock.json 含三个键且 `backtrader_60bps_rt.sharpe_daily_resampled` 为有限数。
  （预估 10-25 min 单线程 Python 循环；脚本须把 trades/equity 增量落盘以便断点续跑。）

### T08 — WF 窗口 runner + window 0（group C, M, deps: T06）

- 目标: 写 `run_wf_window.py` 并自验 window 0。
- 文件: `FH/run_wf_window.py`（新建）；产物 `FH/results/se_h3_wf_window_0.json` + `se_h3_wf_trades_0.csv`
- 内容: `--window K`；窗口边界严格按 fixed runner L254-259 算法（train 525600/test 262800/step 262800，
  从对齐索引算），并内置 §1.5 的 7 组 ISO 边界表做断言（边界不符即 exit 1——防数据漂移）；
  **信号只在窗口切片内建**（镜像 L263-273）；输出窗口指标（sharpe_daily_resampled、ann、MDD、PF、
  n_trades、trades csv）。
- 验收: `python3 FH/run_wf_window.py --window 0` exit 0，json 含 `test_start_iso=="2022-11-20 16:01:00"`；单窗运行 <5 min。

### T09-T14 — window 1-6 执行（group D, 每个 S, deps: T08）

- 目标: 6 个窗口并行跑（每窗一个 cheap agent）。
- 文件: 各写 `FH/results/se_h3_wf_window_{K}.json` + `se_h3_wf_trades_{K}.csv`（K=1..6，互不相交）
- 验收（每窗）: `python3 FH/run_wf_window.py --window K` exit 0；json 的 `test_start_iso` 与 §1.5 表第 K 行一致、`n_trades ≥ 0`、`sharpe_daily_resampled` 有限。
- 备注: window 3（2024-05→2024-11）与 quick_verify 的 2024 自然年切片重叠最高，是直观对照点，但不作验收门槛。

### T15 — 聚合 + 证据包（group E, M, deps: T07, T09-T14）

- 目标: 合并 7 窗 + 全历史 + fee shock，出判决证据包。
- 文件: `FH/aggregate_verdict.py`（新建）；产物 `FH/results/se_h3_metrics.json`、`FH/VERDICT.md`
- 内容: 读 7 窗 json → OOS mean Sharpe / ann / worst MDD / mean PF + bootstrap CI（seed 42、10000 次，
  代码逐行复制 fixed runner L288-299）；合并 T07 全历史与 fee shock；映射进 gates 字段
  （sharpe_daily→OOS Sharpe 等），`certify_metrics` 跑一遍并**如实记录 G5/G7 因缺字段 FAIL=NOT_RUN**；
  VERDICT.md 含：与 H3 baseline 对照表（§1.5 锚点值）、SPEC 证伪条件逐条判定、60bps Sharpe、
  给主线的一句话证据摘要（不写 KEEP/KILL 结论，那是主线的事）。
- 验收: `python3 FH/aggregate_verdict.py` exit 0；`python3 -c "import json; m=json.load(open('FH/results/se_h3_metrics.json')); assert m['oos']['n_windows']==7 and 'bootstrap_ci_lower' in m['oos'] and 'fee_sensitivity' in m"`；VERDICT.md 含 7 窗逐窗 Sharpe 表。

---

## 3. 依赖与并行波次

- wave 1（group A，4 并行）: T01, T02, T03, T04 —— 文件互不相交，规格已锁定。
- wave 2（group B，2 并行）: T05, T06 —— parity 双锚定，**必须先全绿才放行执行波**。
- wave 3（group C，2 并行）: T07, T08 —— 重 CPU，分机器跑（Mac / server-105 各一）。
- wave 4（group D，6 并行）: T09-T14。
- wave 5（group E，1）: T15。

关键路径: T02/T03/T04 → T06 → T08 → T09-14 → T15。T06 是质量闸门（parity 不绿，后面全是垃圾数）。

## 4. Out of scope（明确不做）

- **不改任何生产/共享代码**：`strategies/_indicators/mtf_xs_pairs_base_20260718.py`、H3 `config.json`、
  `_shared/`、`run_btcsol_variants_fixed.py`、data parquets —— 一律只读 import。
- 不跑 G5 双框架 CV（freqtrade/backtesting.py adapter）与 G7 DSR/CPCV —— 标 NOT_RUN，留给后续 workstream。
- 不做参数扫荡（slope lookback / stop 值只有一个预注册组合，cycle-46 纪律）。
- 不碰 ETH 腿、不碰 22bps maker 成本模型（属 H3-execution-maker workstream）。
- 不写 ledger / compare 页面 / multica issue 评论 / KEEP-KILL 判决 —— 证据包交回研究主线。
- 不删改 `signal-enhance-h3/` 既有产物（quick_verify*、charts、SUMMARY.md 等）。
- 不做 git 操作。

## 5. 跨 workstream 冲突警告

1. **只读依赖钉死**: 本 workstream 的 parity 验收（T05/T06）和边界断言（T08）依赖以下文件字节级稳定：
   `strategies/_indicators/mtf_xs_pairs_base_20260718.py`、`strategies/mtf_xs_pairs_1m_15m_2h_h3_20260718/config.json`、
   `H3-variants-h1h2h4/run_btcsol_variants_fixed.py`、`data/perp_1m/*.parquet`、`data/funding/*.parquet`、
   `H3-baseline-repro/metrics.json`。任何管清理/重构的 workstream（_shared 收敛、adapter 合并、
   硬编码路径替换）若动到这些文件，会让 T05/T06/T08 假阴性失败 —— sprint 期间请冻结。
2. **H3-execution-maker workstream**: 同族策略，必须各自用独立输出目录；本 workstream 只写
   `signal-enhance-h3/full_history/`。
3. **CPU 争用**: wave 3-4 共 8 个 Python 长循环（单核各 2-25 min）；Mac + server-105 分摊，
   单机同时 ≤4 个窗口跑，避免 swap 干扰耗时度量（耗时也是证据的一部分）。
4. **funding 数据双源问题**（§1.4）若其他 workstream 在做数据层统一，注意 quick_verify_2024.json
   的旧数与新管线结果不可直接比，差异来自 funding 源而非策略逻辑。
