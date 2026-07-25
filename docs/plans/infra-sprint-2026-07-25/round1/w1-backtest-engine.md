# W1 回测与验证引擎统一 — Round-1 任务包

> Sprint 目标：让任何新策略 5 分钟内接入统一引擎 + 双框架 CV + fee shock 管线。
> 执行方式：2×128 并行 cheap agent（caocao-m3）。每个任务 <30 min、文件隔离、机械验收。
> Python 一律 `/Users/mark/sdk/mamba-envs/trading/bin/python3`（下称 `$PY`）。
> 所有 pytest 命令的工作目录均为 `/Users/mark/multica/quant-loop`。

---

## 1. Current-state findings（已读码核实，附 file:line）

**已统一的部分（不要重做）**

- `_shared/run_backtest.py`（307 行）已是向量化 per-bar 复利引擎：`np.cumprod` 在 `_shared/run_backtest.py:283`，`_apply_trade` 切片累加在 :130-168。cost_mode `"fill"` 与 backtrader COMM_PERC 对齐（docstring :21-33）。**"引擎向量化"任务已完成，无需再做**。
- `_shared/validation/compute_metrics.py`（118 行）9-key schema 唯一事实源；max_dd 负号约定（:41-43）；trade_pnls 传入时 win_rate 按 trade（:90-93）。
- `validation/gates.py` 已委托 `_shared/gates/enforce.py:certify_metrics`（gates.py:165）；G7 = DSR 非 Bonferroni（gates.py:143-150）；缺失字段 = MISSING_FIELD FAIL（enforce.py:53-62, 98-101）。Phase B 门禁统一**已落地**。
- `validation/metrics.py` 已是 compute_metrics 薄包装（:1-7, :26 import）。
- `validation/adapters/` 已有 4 个共享 replay adapter（backtrader 117 / freqtrade 265 / vectorbt 112 / native_engine 122 行）。
- `validation/generic_harness.py`（420 行）contract-v2 通用管线已可用：`signals.py` 路由（oos_harness.py:211-214）、framework 缺失记 skip 不崩（generic_harness.py:315-324）、G1-G7 走同一 evaluate_gates。
- `_shared/templates/` 已有 strategy_contract_v2.py / run_strategy.py / preregistered_cpcv.py / example_strategy.py + 测试。
- 基线测试绿：`pytest validation/ _shared/ -q` → **142 passed, 3 skipped, 4.57s**（2026-07-25 实测）。

**缺口 / 问题（本 sprint 要修）**

1. **框架引擎未安装**：`$PY -c "import backtrader/freqtrade/vectorbt"` 三个全 ModuleNotFoundError（实测）。generic harness 的 framework leg 永远 skip → G5 永远 MISSING_FIELD FAIL → 本机根本跑不了双框架 CV。`validation/requirements.txt:6-8` 已声明依赖但 env 没装。
2. **Fee shock 不在管线里**：统一管线第 5 步（60bps fee shock Sharpe>0）在 `validation/` + `_shared/` 中**不存在**（grep 全仓库只有 research/swarm 脚本和个别策略自带）。唯一可参考实现：`research/swarm/2026-07-25/H3-variants-h1h2h4/run_btcsol_variants_fixed.py:313 fee_shock_metrics`（daily 重采样 + 按日 drag）。
3. **73 个 adapter ~30k LOC**：实测 `find strategies -name "framework_adapter_*.py"` = 73 文件 / 29,954 行（32 backtrader / 30 freqtrade / 11 vectorbt）。其中 **62 个在 `_graveyard/`**（死代码），仅 **11 个在 7 个活跃策略目录**：vpvr_carry_term_8h（3）、vpvr_xs_smart_routing_15m（2）、vpvr_xs_basis_zscore_15m_funding_filter（2）、vol_breakout_vpvr_val_fade_1h_5m（2）、momentum_trend_btc_only_softer_stop_1h（1）、pairs_cointegration_1d（1）。
4. **generic pipeline 采用率 = 0**：活跃策略目录中 `signals.py` 数量 = 0（实测 `ls strategies/*/signals.py` 无结果）。22 个活跃 `strategy.py` 自带 `def run_backtest`；0 个 import `_shared.run_backtest`。
5. **文档漂移**：`validation/README.md` gate 表仍是旧口径——G7 写 "one-sided t-test p<0.0125 (Bonferroni)"、G3/G4 与代码互换（代码：G3=max_dd>G-0.25, G4=PF>1.5；README 写反）。
6. **vectorbt 算了不用**：`evaluate_gates` 只消费 backtrader+freqtrade（gates.py:134-137）；vectorbt leg 的 metrics 进 report 但不进任何 gate。
7. **PF 口径双定义**：`compute_metrics.py:84-87` profit_factor 按 bar return 算；`validation/metrics.py:84-91` 按 trade pnl 算。gate G4 用的是后者，但 compute_metrics 输出的 9-key dict 里 PF 是前者——同一个 key 两个含义。
8. **默认 3 窗 vs 规范 7 窗**：oos_harness.py:194 `--windows default=3`，generic_harness.py:209 `n_windows=3`；总方案 §6 要求全历史 7 窗。
9. **CI 脚本 macOS 不兼容**：`validation/ci/validate_changed_variants.sh` 用 `mapfile`（bash≥4），macOS 自带 bash 3.2 跑不了。
10. **硬编码 /home/smark 仍有 151 个 .py 文件**（全仓库含 graveyard；`_shared/`+`validation/` 子集属 W1，其余归别的 workstream）。

---

## 2. Tasks

并行组规则：**同组任务可同时跑，文件集互不相交**；跨组按 G0 → G1 → G2 波次。

### G0（第一波，7 个任务全并行）

**T1 — 安装框架 CV 引擎到 trading env**
- 目标：让 backtrader/freqtrade/vectorbt 在本机可 import，打通双框架 CV 的物理前提
- 触及：无仓库文件；仅 `$PY -m pip install`（版本按 `validation/requirements.txt:6-8`：backtrader>=1.9.78, freqtrade>=2024.9, vectorbt>=0.26）。freqtrade 安装失败（py3.12 兼容/编译问题常见）时降级为 backtrader+vectorbt，并在任务结果里如实报告
- 验收：`$PY -c "import backtrader, freqtrade, vectorbt; print('ok')"` exit 0（或文档化的部分成功）+ `cd quant-loop && $PY -m pytest validation/test_generic_harness.py -q` 仍绿
- size: S | deps: 无 | group: **G0**
- 注意：这是共享环境变更，全 sprint 只允许执行一次

**T2 — fee shock 模块沉淀到 _shared**
- 目标：新建 `_shared/validation/fee_shock.py`，把 H3-variants runner 的 fee_shock_metrics（`research/swarm/2026-07-25/H3-variants-h1h2h4/run_btcsol_variants_fixed.py:313-345`）提炼为纯函数：`fee_shock_metrics(equity, trades, extra_rt_bps, per_trade_fraction=0.005) -> dict`（sharpe/annualized/total/max_dd），外加 `fee_shock_sweep(equity, trades, bps_list)` 便捷函数
- 触及：新建 `_shared/validation/fee_shock.py`、`_shared/validation/test_fee_shock.py`
- 验收：`cd quant-loop && $PY -m pytest _shared/validation/test_fee_shock.py -q` 全绿；含已知向量测试（extra_rt_bps=0 时输出 == 原始 equity 指标；手算 2-trade 案例精确匹配）
- size: M | deps: 无 | group: **G0**

**T3 — 活跃策略目录 11 个 adapter 退役（part A：4 目录）**
- 目标：删除 `vpvr_carry_term_8h_20260711`（3 个）、`vpvr_xs_smart_routing_15m_20260715`（2 个）、`vpvr_xs_basis_zscore_15m_funding_filter_20260712`（2 个）、`momentum_trend_btc_only_softer_stop_1h_20260712`（1 个）目录下的 `framework_adapter_*.py` + 对应 `test_framework_adapter_*.py` + 相关 `__pycache__`；保留 `framework_adapter_report*.json` 作证据
- 触及：上述 4 个策略目录内文件（不碰 strategy.py / config.json / data_loader.py）
- 验收：`find strategies -name "framework_adapter_*.py" -not -path "*_graveyard*" | grep -E "carry_term|smart_routing|basis_zscore|softer_stop" | wc -l` == 0；`cd quant-loop && $PY -m pytest validation/ _shared/ -q` 仍 142+ 绿
- size: M | deps: 无 | group: **G0**

**T4 — 活跃策略目录 adapter 退役（part B：2 目录）+ graveyard adapter 清点**
- 目标：删除 `vol_breakout_vpvr_val_fade_1h_5m_20260714`（2 个）、`pairs_cointegration_1d_20260709`（1 个）目录下的 adapter + 测试；另删除 `strategies/_graveyard/` 下全部 62 个 `framework_adapter_*.py` 及其 `__pycache__`/*.pyc（保留 report JSON）。pairs_cointegration 的 freqtrade CV 历史结论以 `data/framework_adapter_report*.json` 和 results/ 为准，不删
- 触及：`strategies/vol_breakout_vpvr_val_fade_1h_5m_20260714/`、`strategies/pairs_cointegration_1d_20260709/` 的 adapter 文件、`strategies/_graveyard/**`
- 验收：`find strategies -name "framework_adapter_*.py" -not -path "*__pycache__*" | wc -l` == 0；`$PY -m pytest validation/ _shared/ -q` 绿
- size: M | deps: 无 | group: **G0**

**T5 — validation/README.md 口径修正**
- 目标：gate 表对齐代码现状：G3=max drawdown > -25%、G4=PF>1.5、G7=DSR(Bailey-LdP 2014) 替代 Bonferroni、T1≥30 trades、补 MISSING_FIELD=FAIL 语义和 contract-v2（signals.py）路由说明；删除 "G7 t-test p<0.0125 Bonferroni" 表述
- 触及：`validation/README.md`（仅此一个文件）
- 验收：`grep -c "Bonferroni" validation/README.md` == 0；`grep -c "Deflated Sharpe" validation/README.md` >= 1
- size: S | deps: 无 | group: **G0**

**T6 — _shared/ + validation/ 范围内 /home/smark 路径清理**
- 目标：`_shared/` 和 `validation/` 下所有 `.py` 的 `/home/smark` 硬编码改为 `Path(__file__).resolve().parents[N]` 相对定位；顺手清掉同文件里的 `sys.path.insert` hack（enforce.py:133 那种）
- 触及：仅 `_shared/**/*.py`、`validation/**/*.py` 中含 /home/smark 的文件
- 验收：`grep -rl "/home/smark" _shared validation --include="*.py" | wc -l` == 0；`$PY -m pytest validation/ _shared/ -q` 绿
- size: S | deps: 无 | group: **G0**

**T7 — 引擎性能预算测试**
- 目标：新建 `_shared/test_run_backtest_perf.py`：合成 2.4M bar（≈4.5 年 1m）+ ~10k trades，断言 `run_backtest` 完成时间 < 60s（宽松阈值，防回归不防调优）；同文件附 100k-bar 精确性 sanity 用例
- 触及：新建 `_shared/test_run_backtest_perf.py`（仅此一个文件）
- 验收：`cd quant-loop && $PY -m pytest _shared/test_run_backtest_perf.py -q` 绿
- size: S | deps: 无 | group: **G0**

### G1（第二波，4 个任务全并行；等 G0 完成）

**T8 — generic_harness 接入 fee shock leg + 7 窗默认**
- 目标：`run_generic_validation` 增加 `fee_shock_bps: tuple = (60.0,)` 参数；full-span native run 之后对每个 shock 档位调用 T2 的 `fee_shock_metrics`，结果写入 `report["fee_shock"]`（含 `fee_shock_sharpe`、`passed_60bps` 布尔）；verdict.json 顶层加 `fee_shock` 字段。同时把 generic_harness.py:209 和 oos_harness.py:194 的默认窗数 3 → 7
- 触及：`validation/generic_harness.py`、`validation/test_generic_harness.py`、`validation/oos_harness.py`
- 验收：`cd quant-loop && $PY -m pytest validation/test_generic_harness.py -q` 绿（含新 fee-shock 用例：shock=60bps 时 report["fee_shock"]["60.0"]["sharpe_daily_resampled"] 存在且 <= baseline sharpe）
- size: M | deps: T2 | group: **G1**

**T9 — PF 口径统一 + vectorbt 处置**
- 目标（a）：`compute_metrics.py` 在 `trade_pnls` 传入时 profit_factor 改按 trade pnl 算（与 metrics.py:84-91 一致），不传时保留 bar-based 并在 docstring 标注口径；同步更新 test_compute_metrics。目标（b）：gates.py 把 vectorbt 窗口纳入 G5 的 framework_means（与 docstring G5 定义一致），或显式从 evaluate_gates 签名/文档移除——二选一，选纳入（与"双框架 CV"目标一致，vectorbt T1 装好后可用）；更新 test_gates
- 触及：`validation/gates.py`、`validation/test_gates.py`、`_shared/validation/compute_metrics.py`、`_shared/validation/test_compute_metrics.py`
- 验收：`cd quant-loop && $PY -m pytest validation/test_gates.py _shared/validation/test_compute_metrics.py validation/test_metrics.py -q` 绿
- size: M | deps: 无（与 T8 文件不相交）| group: **G1**

**T10 — 新策略 scaffold 工具（5 分钟接入的核心交付物）**
- 目标：新建 `scripts/new_variant.py`：`python scripts/new_variant.py <name> --timeframe 1h --symbols BTCUSDT,SOLUSDT` 生成 `strategies/<name>_{date}/` 骨架：config.json（含 instruments/timeframe/fees/sizing）、data_loader.py（薄封装现有数据目录，委托 `_shared` 或复制 data_loader 通用模式）、signals.py（contract-v2 模板 + DEFAULT_CONFIG + 一个 toy 双均线示例信号）、README 指针注释。基于 `_shared/templates/strategy_contract_v2.py` 的契约检查内置进 scaffold 自检
- 触及：新建 `scripts/new_variant.py`、`scripts/test_new_variant.py`；不改动 `_shared/templates/` 现有文件
- 验收：`cd quant-loop && $PY scripts/new_variant.py sprint_smoke_$(date +%s) --timeframe 1h --symbols BTCUSDT` 后，`$PY -m validation.oos_harness --variant <生成的目录名> --frameworks native --windows 2` exit code ∈ {0,1}（不 crash，harness error=2 算失败）；`$PY -m pytest scripts/test_new_variant.py -q` 绿。**验收后删除 smoke 目录**
- size: M | deps: 无 | group: **G1**

**T11 — CI 脚本 macOS 兼容修复**
- 目标：`validation/ci/validate_changed_variants.sh` 去掉 `mapfile`（bash 3.2 不兼容），改 portable `while IFS= read -r` 收集；`set -euo pipefail` 保留；行为不变
- 触及：`validation/ci/validate_changed_variants.sh`（仅此一个文件）
- 验收：`bash -n validation/ci/validate_changed_variants.sh` 通过；macOS `/bin/bash validation/ci/validate_changed_variants.sh HEAD HEAD` 输出 "no strategy variant changes" 且 exit 0
- size: S | deps: 无 | group: **G1**

### G2（第三波，2 个任务并行；等 G1 完成）

**T12 — 真实策略迁移样板：pairs_cointegration_1d 接入 generic pipeline**
- 目标：给 `strategies/pairs_cointegration_1d_20260709/` 写 `signals.py`（contract v2：把现有 strategy.py 的信号逻辑包成 `generate_signals(df, cfg)`， equity walk 全部交 `_shared.run_backtest`），使其走 generic 管线复现 legacy 结论（该策略历史 OOS Sharpe 3.60、freqtrade CV 曾通过）
- 触及：`strategies/pairs_cointegration_1d_20260709/signals.py`（新建）、允许小幅改 `data_loader.py` 签名对齐 `load_all(symbols, timeframe)`；不改 strategy.py 原逻辑
- 验收：`cd quant-loop && $PY -m validation.oos_harness --variant pairs_cointegration_1d_20260709 --frameworks native,backtrader --windows 3` 产生的 verdict.json 含 `"pipeline": "generic"`；native leg mean OOS Sharpe 与 legacy 路径同 run 偏差 < 5%（任务内跑两次对比，数据量 1d 级别，远低于 2 分钟上限）
- size: M | deps: T8（fee shock 字段）、T3/T4（该目录 adapter 已删）| group: **G2**

**T13 — 新策略 5 分钟接入 ONBOARDING 文档**
- 目标：新建 `quant-loop/docs/onboarding-validation.md`：contract v2 一页纸 quickstart——scaffold（T10）→ 写 signals.py → 7 窗双框架 CV + 60bps fee shock 一条命令 → verdict.json 字段解释 → G1-G7+T1 阈值表（与代码一致的版本）→ 常见坑（off-bar trade 被 skip、G5 MISSING_FIELD）
- 触及：`quant-loop/docs/onboarding-validation.md`（仅此一个文件）
- 验收：文档中每条命令都在 T12 的迁移样板目录上真实跑过（agent 必须在任务内执行并把 exit code 写进文档）；`grep -c "fee_shock" quant-loop/docs/onboarding-validation.md` >= 1
- size: S | deps: T10、T12 | group: **G2**

---

## 3. Out-of-scope（明确不做）

- **server Go 侧 gate bug**（`server/internal/gate/gate.go:115-117,131` skip-pass）——属服务端 workstream，W1 不碰 `server/`
- **results-ledger / verdict 语义拆分**（framework_consistent vs profitable 字段）——ledger workstream
- **`/home/smark` 全仓库清理**——W1 只清 `_shared/` + `validation/`（T6）；strategies/、research/、scripts/ 下的归别人
- **graveyard 策略的信号代码修改 / 重跑验证**——cycle-46 纪律，已 KILL 家族不重验
- **signal-enhance-h3 全历史验证**——P2 研究任务，不是基建
- **paper trading harness 重建**——总方案 §11 明确不做
- **数据 manifest / fetcher / aggTrades 历史回补**——数据 workstream
- **run_backtest 向量化**——已完成（finding #1），勿重复
- **任何 autopilot / multica issue / launchd 变更**——运维 workstream
- **对比页（compare）前端**——展示层 workstream

---

## 4. Cross-workstream conflict warnings（给 parent）

1. **T1 改共享 mamba env**（pip install backtrader/freqtrade/vectorbt）——全 sprint 只允许一个 agent 执行一次；若有其他 workstream 也要动 trading env 依赖，必须串行。
2. **T3/T4 删除 `strategies/` 下文件**（活跃 7 目录 adapter + graveyard 62 个）——若 cleanup/archival workstream 也在动 `strategies/` 或 `_graveyard/`，需按目录划界：W1 只删 `framework_adapter_*.py` / `test_framework_adapter_*.py` / 对应 `__pycache__`，不碰其他文件。
3. **W1 声称拥有**：`validation/` 全目录、`_shared/validation/`、`_shared/run_backtest.py`（本 sprint 无改动任务）、`_shared/gates/`（本 sprint 无改动任务）、`scripts/new_variant.py`（新建）。若 gate/ledger workstream 要改 `_shared/gates/enforce.py` 或 `validation/gates.py`，与 T9 冲突——T9 只改 `validation/gates.py`，enforce.py 本 sprint W1 不碰。
4. **T6 与任何"全仓 /home/smark 清理"任务重叠**——建议由 W1 只做 `_shared/`+`validation/` 子集，其余目录划给清理 workstream，避免两个 agent 改同一文件。
5. **freqtrade 安装风险**：py3.12 + macOS 上 freqtrade 经常装不上；T1 若部分失败，T9（vectorbt 纳入 G5）和 T12（backtrader leg）仍可进行，但"双框架 CV"目标降级为单框架——需要 parent 决策是否接受。
