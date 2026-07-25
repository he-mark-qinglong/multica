# W3 数据管道与策略脚手架 — Round-1 任务分解（w3-data-scaffold）

> 生成：2026-07-25，round-1 planning agent（只读本机代码核实，未改任何代码）。
> 执行方式：2×128 并行 cheap agents（caocao-m3）。每个任务 <30min、独立、文件级、可机械验收。
> 并行纪律依据 `docs/plans/multica-quant-permanent-loop-2026-07-25.md` §3.1：本工作流全是基础设施/执行面，可 swarm。

---

## 1. 现状盘点（全部为本机实读核实，file:line 引用）

### 1.1 数据资产（`quant-loop/data/`，11G）

| 目录 | 内容 | 覆盖范围（实测） |
|---|---|---|
| `perp_1m/` | BTC/ETH/SOL 3 币 1m klines（531M） | BTC 2019-09-08→2026-07-17，ETH 2019-11-27→，SOL 2020-09-14→（`data/manifests/perp_resampled_2026-07-24.yaml:23-50`，含 sha256+行数+连续性校验全过） |
| `perp_5m/`, `perp_15m/` | 同 3 币，由 1m resample | 同上，identity_checks 全 `passed: true`（manifest:109-172） |
| `perp_30m/` | 7 币（BTC/ETH/SOL/BNB/DOGE/AVAX/LINK）原生抓取 | AVAX 实测 79,296 行；schema `open_time,open,high,low,close,volume,close_time,quote_volume` |
| `perp_2h/` | BTC/ETH/SOL | BTC 实测 30,048 行，schema 同 30m |
| `funding/` | 7 币 8h funding（1.7M，parquet+csv+fetcher） | **仅 2021-11-20→2026-07-17**（BTC 实测 5,100 行）——比 klines 短 2 年，2019-2021 无 funding。schema `ts,symbol,fundingRate,markPrice`（`data/funding/README.md`） |
| `trades/` | 7 币 aggTrades（9.8G） | **仅 2026-01-01→2026-07-17**（BTC 实测 323,913,759 行）——微结构研究只有半年数据 |
| `features/` | feature_matrix 仅 BTC/ETH 两个 parquet | — |
| `tradfi_1d/` | BTC-USD/QQQ/SPY 日线 | — |
| `vpvr/` | **空目录** | — |
| `manifests/` | 3 个 yaml；只有 `perp_resampled_2026-07-24.yaml` 是完整 hash 校验清单 | 其余目录无 manifest |

关键缺口（写策略前必须知道的）：funding 缺 2019-2021；aggTrades 只有 2026 上半年；30m 是唯一覆盖 7 币的 klines 周期；数据最新只到 2026-07-17/18（落后约 1 周）。

### 1.2 数据加载现状

- **无共享数据加载器**：`_shared/` 下无 data 模块（实读 `_shared/` 列表：execution/ gates/ indicators/ regime/ sizing/ templates/ validation/ validators/ + run_backtest.py）。
- **25 个非 graveyard 策略各有 bespoke `data_loader.py`**（另 36 个在 `_graveyard/`），命名/路径约定互不统一。例：`strategies/mtf_xs_pairs_1m_15m_2h_h3_20260718/data_loader.py:14` 用策略目录内本地 `data/` 副本——直接违反 contract v2「No local data copies」规则（`_shared/templates/strategy_contract_v2.py` docstring 规则 3）。
- **~20 个非 graveyard 策略目录带本地 `data/` 副本**（实测 du：loid_vpvr_confluence 84M、vol_breakout_vpvr_val_fade 21M 等），违反同一规则。
- `run_strategy.py` 的 `load_bars_dir`（`_shared/templates/run_strategy.py:82-97`）只认 `{SYMBOL}.parquet` 平铺命名，**不认识** `data/perp_{tf}/{SYM}_{tf}.parquet` 实际约定——通用 runner 与真实数据布局脱节。
- contract v2 + 通用 runner + 示例策略已存在（`_shared/templates/strategy_contract_v2.py`、`run_strategy.py`、`example_strategy.py` 及其测试），**但缺**：SPEC 模板、目录脚手架生成器、metrics.json 校验器。

### 1.3 `/home/smark` 硬编码（本机 `/home/smark` 不存在，实测 `ls` 报错——这些文件在 Mac 上全坏，只能在 server-105 跑）

- 全 quant-loop：537 处 / 238 文件。
- 非 graveyard、非 data/：**136 文件 = 79 个 .py + 57 个非代码（27 .md / 24 .json / 3 .sh / 3 其他）**。
- graveyard：71 个 .py（**不动**，归档证据）。
- 分布头部（.py，非 graveyard）：scripts/ 9、research/ofi/ 8、live_data/ 6、strategies/mtf_vpvr_edge_zscore 6、vpvr_xs_smart_routing 3、vpvr_edge_zscore_multi_tf 3、vpvr_carry_term 3、research/calibration 3 …
- 典型形态（实测）：`Path("/home/smark/multica/quant-loop/live_data")`、`DEFAULT_OUT_DIR = "/home/smark/multica/quant-loop/data/funding"`。
- **当前无任何 env-var 约定**：`_shared/` 内 grep `os.environ` 零命中。需要先立 `_shared/paths.py` 再迁移。

### 1.4 成本模型现状

- 权威实现已存在：`_shared/execution/cost_model.py:113-138` `apply_cost()`；`BINANCE_FUTURES`（:84-89）= 4bps fee + 7bps slippage/side = **22bps 往返**（SMA-34900/34913 批准，常量 import 自 `backtest/factor_backtester.py`，单一来源）。spot 路径是未批准的 sqrt-impact（:17-23 docstring 明确警告勿用于 perp）。
- 但 `run_strategy.py:143` 与 `:226` 默认 `cost_bps_rt=24.0`——与批准的 22bps 不一致（legacy）。
- 非 graveyard 策略里大量内联成本常量（grep `0.0004/0.0008/0.0011/0.0022/0.0024/fee_bps` 命中 30+ 文件，含 h1-h4 的 framework_validate.py、sizing_sweep.py 及各 strategy.py/framework_adapter_*.py）。
- metrics 9-key schema 已统一：`_shared/validation/compute_metrics.py:22-56`（sharpe_daily, annualized_return, max_drawdown_pct, profit_factor, n_trades, n_bars, win_rate, calmar, sortino）。

---

## 2. 任务列表（14 个）

约定：
- **env var 名统一为 `QUANT_LOOP_ROOT`**（指向 quant-loop/ 的父级仓库内路径解析基准）；未设置时从 `__file__` 推导，保证零配置可用。
- 所有迁移类任务**只许 import `_shared/paths.py`，禁止各自新写路径推导逻辑**（import 模式照抄 `cost_model.py:36-52` 的双模式 sys.path 处理）。
- parallel-group：同组任务文件不相交，可同批并行；跨组有依赖顺序。

### Phase A — 地基（先行，后续全部依赖）

**T1. `_shared/paths.py` 路径解析 helper**
- 目标：提供 `quant_loop_root()` / `data_root()` / `live_data_root()`，读 `QUANT_LOOP_ROOT` env，缺省从 `__file__` 推导（paths.py 在 `_shared/` 下，`parents[1]` = quant-loop/）。
- 文件：新建 `quant-loop/_shared/paths.py`、`quant-loop/_shared/test_paths.py`
- 验收：`cd quant-loop && /Users/mark/sdk/mamba-envs/trading/bin/python3 -m pytest _shared/test_paths.py -q` 全过；且 `QUANT_LOOP_ROOT=/tmp/x python3 -c "from _shared.paths import data_root; print(data_root())"` 输出 `/tmp/x/data`
- 大小 S；依赖无；组 **P0**

**T2. `_shared/data_loader.py` 统一数据加载器**
- 目标：实现 `load_bars(symbol, tf)`（按 `data/perp_{tf}/{SYM}_{tf}.parquet` 约定+manifest 校验）、`load_funding(symbol)`、`load_aggtrades(symbol, start, end, columns=None)`（必须列裁剪+时间过滤，9.8G 全读不可接受）、`available(symbol)` 覆盖查询；全部走 T1 的 paths。附带把 `run_strategy.load_bars_dir` 的命名约定说明写进 docstring（不改 run_strategy 行为——那是 T14）。
- 文件：新建 `quant-loop/_shared/data_loader.py`、`quant-loop/_shared/test_data_loader.py`（合成小 parquet 测试 + 一个真实 BTCUSDT_15m 头 100 行只读冒烟）
- 验收：pytest 全过；`python3 -c "from _shared.data_loader import load_bars; df=load_bars('BTCUSDT','15m'); assert len(df)==240392"`（行数对 manifest:64）
- 大小 M；依赖 T1；组 **P0b**

**T3. 数据盘点生成器 + inventory manifest**
- 目标：写 `scripts/build_data_inventory.py`，扫描 data/ 生成 (a) `data/README.md` 覆盖矩阵（人读）(b) `data/manifests/inventory.yaml`（机读：每目录×币种×行数×首末时间，供 §5 数据可得性预检）。提交生成物。**只写这 3 个文件，不改任何 parquet**。
- 文件：新建 `quant-loop/scripts/build_data_inventory.py`、`quant-loop/data/README.md`、`quant-loop/data/manifests/inventory.yaml`
- 验收：`python3 scripts/build_data_inventory.py --check` 幂等（重跑无 diff）；`python3 -c "import yaml; yaml.safe_load(open('data/manifests/inventory.yaml'))"` 通过；inventory 中 funding/BTCUSDT first_ts == 2021-11-20
- 大小 S；依赖 T1（用 paths）；组 **P0c**（与 T1/T2 文件不相交）

### Phase B — `/home/smark` 迁移（6 个 shard，全部只改 .py/.sh，互不交叠）

统一规则（写进每个 shard 的 prompt）：
1. 只改指派目录内**非 _graveyard** 的 `.py`/`.sh`；`.md`/`.json`/`.log` 是历史证据，**禁改**。
2. `framework_adapter_*.py` **跳过不改**（另一工作流在做 adapter 收敛，防冲突）。
3. 替换为 `from _shared.paths import ...`（import 模式照 `cost_model.py:36-52`），禁止硬编 `/Users/mark`。
4. 每个改过的 .py 必须 `python3 -m py_compile` 通过。

**T4. shard: scripts/**
- 文件：`quant-loop/scripts/` 下 9 个含 `/home/smark` 的 .py + 3 个 .sh（grep 清单执行时用 `grep -rl '/home/smark' scripts --include='*.py' --include='*.sh'` 现取）
- 验收：`grep -rn '/home/smark' scripts --include='*.py' --include='*.sh' | wc -l` == 0；全部 py_compile 过
- 大小 M；依赖 T1；组 **P1**

**T5. shard: live_data/**
- 文件：`quant-loop/live_data/` 下 6 个 .py（fetch_binance_usdm_4h.py、fetch_binance_usdt_15m.py、verify_*.py、refresh_klines_*.py 等）
- 验收：同 T4 模式，目录 grep == 0 + py_compile
- 大小 S；依赖 T1；组 **P1**

**T6. shard: research/（ofi 8 + calibration 3 + validation 1 + spillover 1 等）**
- 文件：`quant-loop/research/` 下全部含 `/home/smark` 的非 graveyard .py
- 验收：同上
- 大小 M；依赖 T1；组 **P1**

**T7. shard: strategies mtf/vpvr-edge 系（活跃候选族）**
- 文件：`strategies/mtf_vpvr_edge_zscore_1m_15m_2h_20260718/`（6）、`strategies/mtf_xs_pairs_1m_15m_2h_h1..h4_20260718/`、`strategies/mtf_h2_vpvr_edge_1m_15m_2h_20260718/`、`strategies/vpvr_edge_zscore_multi_tf_1m_15m_2h_20260720/`、`strategies/vpvr_edge_zscore_15m_only_20260720/`、`strategies/impl_vpvr_multi_tf_funding/` 内含 /home/smark 的 .py（跳过 framework_adapter_*）
- 验收：这些目录 grep == 0 + py_compile；`strategies/_indicators/tests/test_mtf_xs_pairs_base_20260718.py` 能跑过（如该测试本身含 /home/smark 则一并迁移）
- 大小 M；依赖 T1；组 **P1**

**T8. shard: strategies 其余目录**
- 文件：非 graveyard strategies/ 下 T7 未覆盖的所有含 /home/smark 的 .py（vpvr_xs_smart_routing、vpvr_carry_term、vol_breakout_*、momentum_*、trend_*、pairs_cointegration、xs_momentum、loid_*、large_order_iceberg、reports/ 等，跳过 framework_adapter_*）
- 验收：同上
- 大小 M；依赖 T1；组 **P1**

**T9. shard: 顶层杂项（backtest/ analysis/ funding_analysis/ validation/ backtests/ workdir/）**
- 文件：这些目录下含 /home/smark 的非 graveyard .py/.sh（workdir 只改 .py，.md 报告禁改）
- 验收：`grep -rl '/home/smark' --exclude-dir=data --exclude-dir=_graveyard --include='*.py' --include='*.sh' quant-loop/ | wc -l` == 0（全局清零，T4-T9 合并效果）
- 大小 M；依赖 T1；组 **P1**

### Phase C — 脚手架与成本统一

**T10. 策略脚手架生成器**
- 目标：`python3 -m _shared.templates.scaffold <strategy_name> --symbols BTC,ETH --tf 15m` 一键生成合规策略目录：`SPEC.md`（模板含 §5 强制四要素：假设一句话/可证伪条件/数据需求+cost-cap 预检/预期成本约束）、`config.json`、`strategy.py`（contract v2 stub，import `_shared.data_loader` 与 `_shared.run_backtest.Trade`，数据加载禁止本地副本）、`tests/test_contract.py`（调 `strategy_contract_v2.check_contract` 合成数据冒烟）、`results/.gitkeep`。
- 文件：新建 `quant-loop/_shared/templates/scaffold.py`、`quant-loop/_shared/templates/SPEC_TEMPLATE.md`、`quant-loop/_shared/templates/test_scaffold.py`
- 验收：pytest 过；且端到端：scaffold 到 /tmp 后 `python3 -m pytest /tmp/<name>/tests -q` 过
- 大小 M；依赖 T2；组 **P2**

**T11. metrics.json 规范 + 校验器**
- 目标：`validate_metrics.py` 校验 metrics.json：9-key schema（compute_metrics.py:28-43）齐全且类型正确 + provenance 字段（strategy, cost_bps_rt, data_window, generated_at）；`METRICS.md` 写清规范。用现有真实样本（如 `strategies/mtf_xs_pairs_1m_15m_2h_h3_20260718/results/metrics.json`）做正例、构造缺字段样本做反例。
- 文件：新建 `quant-loop/_shared/validation/validate_metrics.py`、`quant-loop/_shared/validation/METRICS.md`、`quant-loop/_shared/validation/test_validate_metrics.py`
- 验收：pytest 过；`python3 -m _shared.validation.validate_metrics strategies/mtf_xs_pairs_1m_15m_2h_h3_20260718/results/metrics.json --report` 能给出通过/缺项结论（缺 provenance 只警告不 fail，历史文件豁免）
- 大小 S；依赖无；组 **P2**

**T12. 内联成本扫描器 + 成本约定文档**
- 目标：`check_inline_costs.py` 扫描非 graveyard 策略 .py 中的硬编码费率/滑点字面量（0.0004/0.0008/0.0011/0.0016/0.0022/0.0024、`fee_bps = <数字>` 等，跳过 framework_adapter_* 与测试期望值），`--report` 打印违例清单、`--enforce` 有违例退出码非零；`COST_CONVENTION.md` 立规：perp 一律 `apply_cost(venue=BINANCE_FUTURES)`（cost_model.py:84-89），spot 路径仅限现货策略。
- 文件：新建 `quant-loop/_shared/execution/check_inline_costs.py`、`quant-loop/_shared/execution/COST_CONVENTION.md`、`quant-loop/_shared/execution/test_check_inline_costs.py`
- 验收：pytest 过（含 seed 违例样本触发 enforce 失败）；`python3 _shared/execution/check_inline_costs.py --report` 输出当前违例数 > 0 的清单
- 大小 S；依赖无；组 **P2**

**T13. run_strategy.py 默认成本对齐批准值**
- 目标：`run_strategy.py:143` 与 `:226` 的 `cost_bps_rt=24.0` 默认值改为从 `cost_model` 批准常量推导（22bps RT），CLI help 注明来源；不改任何调用方传参行为。
- 文件：`quant-loop/_shared/templates/run_strategy.py`、对应测试（新建或扩充 `quant-loop/_shared/templates/test_run_strategy_cost.py`）
- 验收：pytest 过；`python3 -m _shared.templates.run_strategy --help` 显示默认 22.0；对 example_strategy 合成数据跑通 main()
- 大小 S；依赖无（cost_model 已存在）；组 **P2**

**T14. mtf_xs_pairs 族（h1-h4 共享 base）迁移到统一成本模型**
- 目标：`strategies/_indicators/mtf_xs_pairs_base_20260718.py` 及 h1-h4 四个目录内的成本计算改走 `apply_cost(venue=BINANCE_FUTURES)`；先在策略目录落 `results/cost_migration_note.md` 记录旧值→新值（若旧值已是 22bps RT 等价则纯换 import，不重跑回测；若不等价，**只记录差异，禁止改结果文件**，交主线判决）。
- 文件：`quant-loop/strategies/_indicators/mtf_xs_pairs_base_20260718.py`、`strategies/mtf_xs_pairs_1m_15m_2h_h{1,2,3,4}_20260718/` 内含成本字面量的 .py（跳过 framework_adapter_*）、各目录新建 `results/cost_migration_note.md`
- 验收：`grep -rn -E '0\.00(04|08|11|22|24)' strategies/mtf_xs_pairs_* strategies/_indicators/mtf_xs_pairs_base_20260718.py --include='*.py' | grep -v test | wc -l` == 0；py_compile 全过；`_indicators/tests/test_mtf_xs_pairs_base_20260718.py` 过
- 大小 M；依赖 T7（同文件先完成路径迁移）、T12（约定文档）；组 **P3**

### Phase D — 数据新鲜度（收尾，可选）

**T15. klines+funding 增量刷新到今日**
- 目标：用迁移后的 `scripts/fetch_binance_usdm_*.py`、`data/funding/fetch_funding.py` 把 perp_1m（3 币）、perp_30m（7 币）、funding（7 币）顶到当前日期；重跑 T3 的 `build_data_inventory.py` 更新 README/inventory；更新 `perp_resampled` manifest 的 last_open_time/sha256（若既有 resample 脚本可用则重跑 5m/15m，否则只更新 1m 段并注明）。
- 文件：`quant-loop/data/perp_1m/*.parquet`、`data/perp_30m/*.parquet`、`data/perp_{5m,15m}/*.parquet`、`data/funding/*.parquet`、`data/manifests/*.yaml`、`data/README.md`（脚本执行+生成物更新；仅当 T4/T5 已落地才可跑）
- 验收：inventory.yaml 中 BTCUSDT 1m last_ts 距今 <24h；`build_data_inventory.py --check` 幂等
- 大小 M（网络抓取，注意 <30min 限制：只增量，不全量）；依赖 T3、T4、T5；组 **P4**

---

## 3. 依赖与排期汇总

```
P0:  T1 ──► P0b: T2 ──► P2: T10
P0c: T3 ────────────────────────► P4: T15
P0:  T1 ──► P1: T4 T5 T6 T7 T8 T9（同组并行，目录互不相交）
                     T4+T5 ─► P4: T15
                     T7 ────► P3: T14（还需 T12）
P2 并行: T10 / T11 / T12 / T13（文件互不相交）
```

## 4. Out of scope（明确不做）

- **不改 `_graveyard/` 内 71 个 .py**——归档证据库，迁移无迭代价值且污染证据。
- **不改 `.md`/`.json`/`.log` 里的 `/home/smark`**（57 个非代码文件）——workdir 运行报告、results/metrics、fetch report 是历史证据，路径是记录的一部分；改它们还可能破坏 hash 校验链。
- **不动 `framework_adapter_*.py`**——adapter 收敛（73→1，generic_harness）属另一工作流；W3 只绕过。
- **不改 `cost_model.py` / `run_backtest.py` / `compute_metrics.py` / `factor_backtester.py` 的常量与算法**——W3 只做接入与默认值对齐，不重定批准值。
- **不删除策略目录内本地 `data/` 副本**（~20 个目录、最大 84M）——本期只在新脚手架禁新增；存量清理涉及证据完整性，留待单独归档任务（建议交 knowledge-curator/strategy-archiver 线）。
- **不补历史数据**（funding 2019-2021、aggTrades 2025 及以前、vpvr 空目录、features 仅 BTC/ETH）——只在 inventory 中如实标注缺口；补数是单独的数据工程决策。
- **不重跑任何策略回测验证成本迁移**（>2min 禁令 + 结果变更属主线判决权）。
- **不给 paper trading harness 续命**（计划 §11 明令）。
- **不做策略 ideation**（§3.1：假设推演单线程，不在本 swarm 内）。

## 5. 跨工作流冲突预警（给 parent）

1. **adapter 收敛工作流**：T4-T9 已排除 `framework_adapter_*.py`；若对方要改策略目录内其他文件（如 strategy.py），与 T7/T8/T14 撞车——建议按目录划线：adapter 工作流只碰 `framework_adapter_*` + `validation/generic_harness.py`。
2. **graveyard 归档工作流**：若本期继续把非 graveyard 策略移入 `_graveyard/`，会与 T7/T8 迁移竞争同一文件——建议归档动作先于 W3 Phase B，或 W3 shard 执行前重新 `grep -rl` 现取清单（已写入任务规则）。
3. **server gate / ledger 工作流**（Go 侧 `server/internal/gate/gate.go` 等）：与 W3 零文件交叠，无冲突。
4. **T13 改 `run_strategy.py` 默认成本 24→22bps** 会改变所有未显式传 cost 的历史对比口径——若 compare/ledger 工作流依赖旧默认重跑，需知会；验收测试里必须固定显式值防隐式漂移。
5. **T15 改 data/*.parquet + manifests**——任何正在进行的回测若并发读同一 parquet，可能读到刷新中途状态；建议 T15 排在所有回测类任务之后的独立批次（组 P4 已隔离）。
