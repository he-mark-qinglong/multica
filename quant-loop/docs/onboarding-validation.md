# 新策略 5 分钟接入 — OOS 验证 quickstart

> 这是一页纸指南，零上下文的人照做即可把一个新策略接入 quant-loop 的
> OOS 验证流水线（G1-G7 + T1 + fee shock）。所有命令的工作目录均为
> `quant-loop/`（即 `/Users/mark/multica/quant-loop`），Python 一律使用
> `/Users/mark/sdk/mamba-envs/trading/bin/python3`（下称 `$PY`）。
>
> 本文档写成于 2026-07-25，作为 W1 验收脚本 `w1_acceptance.sh` 的逐段
> 文字版；事实来源严格以 `validation/gates.py` / `validation/oos_harness.py` /
> `validation/generic_harness.py` / `_shared/run_backtest.py` 为准。
> `validation/README.md` 文档是 W0 之前的旧版，口径与代码不同步，
> 一切以源码注释为准。

---

## 1. TL;DR — 五条命令

```bash
cd /Users/mark/multica/quant-loop

# 1) 脚手架（或手动三步，见 §3 缺失兜底）
python3 -m _shared.templates.scaffold <name> --symbols BTCUSDT --tf 15m

# 2) 写 signals.py（contract v2，§4）+ config.json + data_loader.py

# 3) 一条命令跑 7 窗双框架 CV + 60bps fee shock
$PY -m validation.oos_harness \
    --variant strategies/<name> \
    --frameworks native,backtrader,freqtrade \
    --windows 7

# 4) 读 verdict.json（§5 字段表 + §6 fee_shock）
cat strategies/<name>/results/validation/verdict.json | jq .

# 5) exit code 语义：0 = PASS，1 = 任一 FAIL，2 = harness 错误
```

---

## 2. Exit code 语义（`validation/oos_harness.py:14-17`）

| exit code | 含义 | 何时出现 |
|----------:|------|----------|
| 0 | G1-G7 + T1 全 PASS，merge 允许 | 全部门禁通过 |
| 1 | 至少一条 gate FAIL，merge 被 block | G1-G7 或 T1 任意一条 FAIL |
| 2 | harness 错误（变体不支持、缺数据、框架崩溃） | signals.py / data_loader.py 不在；contract v2 校验失败；数据 parquet 缺失；框架引擎抛异常等 |

> 任何 gate FAIL 都是 BLOCK；缺字段（如框架引擎没装导致 G5 无数据）记
> `MISSING_FIELD` = FAIL，不是 PASS（见 `validation/gates.py:135-143` 与
> `_shared/gates/enforce.py::certify_metrics`）。

---

## 3. Scaffolding — 新建策略目录

### 3.1 正常路径（脚手架已落地）

脚手架工具位于 `_shared.templates.scaffold`（CLI 入口：
`scripts/new_variant.py`，W3-T10 即将合并的便捷封装）。用法：

```bash
cd /Users/mark/multica/quant-loop
python3 -m _shared.templates.scaffold <name> \
    --symbols BTCUSDT,ETHUSDT --tf 15m
# 或：python3 scripts/new_variant.py <name> --symbols BTCUSDT --tf 15m
```

生成 `SPEC.md` + `config.json` + `strategy.py`（contract v2 桩，返回 `[]`，
check_contract 一定 PASS）+ `tests/test_contract.py`（开箱可跑）+ `README.md`。

### 3.2 兜底：手动三步（脚手架未落地时）

如果 `scripts/new_variant.py` 与 `_shared.templates.scaffold` 都不可用，
手工建目录，文件清单按 contract v2：

1. 建目录 `strategies/<name>_<yyyymmdd>/`。
2. 写 `config.json`，必含字段：
   - `instruments`：list[str]（参与交易的 symbols）
   - `timeframe`：str（`1m` / `5m` / `15m` / `1h` / `1d` 等，见 §7 映射表）
   - `fees_bps_per_side`：float
   - `slippage_bps_per_side`：float
   - `starting_capital_usd`：float（默认 100_000）
   - `sizing.per_signal_weight_pct`：float
   - `sizing.max_gross_exposure_pct`：float
3. 写 `data_loader.py`，暴露 `load_all(symbols, timeframe) -> dict[str, pd.DataFrame]`。
4. 写 `signals.py`，暴露 `generate_signals(bars, config) -> list[Trade]` 与
   `DEFAULT_CONFIG`（contract 合成 smoke 用）。

> 一旦脚手架工具落地，§3.2 应改写为 `python3 -m _shared.templates.scaffold ...`。

---

## 4. Contract v2 — signals.py 必须遵守

来源：`_shared/templates/strategy_contract_v2.py:1-40` + `_shared/run_backtest.py:77-87`。

### 4.1 函数签名

```python
def generate_signals(
    bars: dict[str, pd.DataFrame],   # symbol -> OHLCV (UTC DatetimeIndex, 至少 close 列)
    config: dict,
) -> list[Trade]:
    ...
```

### 4.2 `Trade` 四字段（`_shared/run_backtest.py:77-87`）

```python
@dataclass(frozen=True)
class Trade:
    entry_ts: pd.Timestamp
    exit_ts: pd.Timestamp
    direction: Literal["long", "short"]
    size_fraction: float = 1.0    # ∈ [0, 1]
```

### 4.3 强制约束

- `entry_ts` / `exit_ts` **必须落在 `bars.index` 上**。off-bar 交易会被
  **静默跳过**，不报错（`validation/generic_harness.py:144-145` 注释：
  "mirrors the engine's off-bar skip"）。
- `DEFAULT_CONFIG` 必须存在，否则 `check_contract` 合成 smoke 用单 symbol
  `SYNTH` 兜底，跑出空仓结果掩盖问题。
- **禁止**在 signals.py 函数体内 lazy import 本目录的兄弟模块。`_load_module`
  导入后立即 `sys.path.remove(str(variant_dir))`
  （`validation/adapters/native_engine.py:47-51`），顶层以外的 import
  会因 `sys.path` 不再包含变体目录而 `ModuleNotFoundError`。
- equity / metrics 一律由 `_shared.run_backtest.run_backtest` 算。**策略层
  禁止自己算 Sharpe / drawdown**——那是 harness 的活。

### 4.4 多 symbol / pairs 策略

合约本身是按主 symbol 走，但多 symbol 策略（pairs / cross-section）需要在
`signals.py` 顶部实现 `_identify_symbol(bars, config) -> str`，从
`config["primary_symbol"]` 或 `config["instruments"][0]` 拿主 symbol，
再按主 symbol 的 `bars.index` 产出 `Trade`。T12 产出的
`strategies/pairs_cointegration_1d_20260709/signals.py` 是真实样板（T12 落地后
路径生效，目前尚未合并）。

---

## 5. verdict.json 字段解释表

来源：`validation/generic_harness.py:265-272`（run_generic_validation 文档字符串）
+ `validation/generic_harness.py:356-358`（顶层字段位置）+ `validation/oos_harness.py:79-80`。
两个 harness（legacy `run_validation` + generic `run_generic_validation`）输出
verdict 形态基本对齐，区别见 §6。

| 字段 | 类型 | 含义 |
|------|------|------|
| `variant` | str | 变体目录名 |
| `pipeline` | str | `"generic"`（contract v2）或 `None`（legacy strategy.py 路径） |
| `timeframe` | str | 来自 `config.json["timeframe"]` |
| `windows` | list[str] | 每个 OOS 窗的 label，例如 `W1[2024-04-23..2026-06-23]` |
| `symbols` | dict | `{sym: {window_label: {framework: metrics}}}`。`framework` ∈ `native` / `backtrader` / `freqtrade` / `vectorbt` |
| `framework_skips` | dict | `{framework: "ExceptionClass: msg"}`。引擎未装或框架 leg 抛异常时记录；**不是 PASS**（G5 缺数据 = MISSING_FIELD = FAIL） |
| `full_native` | dict | `{sym: metrics}` 全期 native 跑出来的指标（G1/G2-full/G3/G4 取这里） |
| `gates` | list[dict] | G1/G2/G3/G4/G5/G6/G7/T1 每条：`{gate, passed, observed, threshold, detail}` |
| `verdict` | str | `"PASS"` 或 `"FAIL"`（所有 gates 都 PASS 才 PASS） |
| `fee_shock` | dict | **T8 完成后才有**。见 §6 |

> 字段集合随 contract 演进，上述为 W1 落地后的稳定版。

---

## 6. fee_shock 字段（T8 完成后）

T8 在 `validation/generic_harness.py:328-349` 给 verdict 顶层加了 `fee_shock`
对象。结构：

```jsonc
"fee_shock": {
  "60.0": {
    "extra_round_trip_bps": 60.0,
    "sharpe_daily_resampled": <float>,
    "annualized_return": <float>,
    "total_return": <float>,
    "max_drawdown_pct": <float>,
    "per_symbol": { "<sym>": { ... same keys ... } }
  },
  "passed_60bps": true   // 仅 generic pipeline 顶层平铺
}
```

实现位于 `_shared/validation/fee_shock.py::fee_shock_sweep`，对每个 bps
档跑一遍费率放大后的指标，60bps 是当前合同约定的默认门槛。

---

## 7. 门禁表 — G1-G7 + T1 阈值（权威口径）

> 来源：`validation/gates.py:8-17`。`validation/README.md` 的门禁表是 W0 旧版，
> 与代码不同步，**一切以源码注释为准**。

| gate | 阈值 | 含义 | 备注 |
|------|------|------|------|
| **G1** | 全期 mean Sharpe ≥ **1.0** | 各 symbol 全期 native Sharpe 的均值 | native 引擎跑全期算 |
| **G2** | min(全期年化, mean OOS 年化) ≥ **15%** | 两边都看，取最小 | 任意一边塌方即 FAIL |
| **G3** | max_drawdown > **-0.25** | 负号约定；观察值 > 阈值即 PASS | 取所有 symbol 中最差者 |
| **G4** | profit_factor > **1.5** | 全期 profit factor 均值（inf 截断 10） | |
| **G5** | 框架 CV（backtrader/freqtrade/vectorbt）mean OOS Sharpe ≥ **1.0** | 最差一档 ≥ 1.0 | **NaN = MISSING_FIELD = FAIL**，不是 skip-pass |
| **G6** | bootstrap 95% CI lower ≥ **0.5** | 10000 次 resample，seed=42（`validation/stats.py`） | pooled OOS daily returns |
| **G7** | Deflated Sharpe Ratio > **0** | Bailey-LdP 2014，`n_trials=100`（家族大小） | 多重检验校正用 DSR |
| **T1** | pooled OOS trades ≥ **30** | 全 OOS 窗累计 trade 数 | 数量不足即 FAIL |

---

## 8. 常见坑（一句话版）

1. **off-bar 交易被静默跳过**——`Trade.entry_ts` / `exit_ts` 不在 bars 上
   时 harness 不报错，只是该笔 trade 不计入 trades，会拉低 G5/G6 间接导致 FAIL。
2. **框架引擎未安装时 G5 = FAIL**，不是 PASS。`framework_skips` 只记录原因，
   缺数据的 gate 在 `enforce.py` 里记为 `MISSING_FIELD` → FAIL。
3. **不要在 signals.py 函数体内 lazy import 本目录兄弟模块**——
   `_load_module` 加载完就 `sys.path.remove(variant_dir)`
   （`validation/adapters/native_engine.py:47-51`），之后再 import 会
   `ModuleNotFoundError`。
4. **timeframe → freq_per_year 映射表**（`validation/generic_harness.py:61-71`）：
   `1m=525600, 5m=105120, 15m=35040, 30m=17520, 1h=8760, 2h=4380, 4h=2190, 8h=1095, 1d=365`。
   未知 timeframe 直接 `ValueError`。
5. **多 symbol / pairs 策略**需要显式 `_identify_symbol` 从 `config` 拿主
   symbol 的 bars（合约按主 symbol 的 `bars.index` 校验 Trade）；参见 T12
   `strategies/pairs_cointegration_1d_20260709/signals.py` 真实样板。
6. **DEFAULT_CONFIG 必须存在**——`check_contract` 合成 smoke 跑依赖它来
   推断 symbol / 时间窗；缺失时 fallback 到 `SYNTH` 单 symbol 兜底，
   会掩盖 contract 错误。
7. **新策略不要自己算 equity / Sharpe**——那是 `_shared.run_backtest` +
   harness 的职责。策略只产 trade schedule。
8. **`signals.py` 内不要 inline re-implement 指标**——VPVR / ATR / RSI /
   regime 必须从 `_shared/indicators/` 引入（contract v2 规则 §1）。

---

## 9. 实测记录（2026-07-25 在 `pairs_cointegration_1d_20260709` 上）

以下命令均在本仓库 commit `28d47a10e`（`upstream/main` HEAD）下实测：

### 9.1 跑 2 窗 native 验证（生产用 7 窗）

```bash
$ cd /Users/mark/multica/quant-loop
$ /Users/mark/sdk/mamba-envs/trading/bin/python3 \
    -m validation.oos_harness \
    --variant pairs_cointegration_1d_20260709 \
    --frameworks native --windows 2
[harness] variant=pairs_cointegration_1d_20260709 tf=1d \
    symbols=['BTCUSDT', 'ETHUSDT', 'SOLUSDT'] windows=2 frameworks=['native']
Traceback (most recent call last):
  ...
  File ".../strategies/pairs_cointegration_1d_20260709/data_loader.py", line 128,
    in load_symbol_1d
    src = source_root / f"fapi_{sym}__1m.parquet"
TypeError: unsupported operand type(s) for /: 'str' and 'str'
$ echo $?
2
```

exit code = **2** — harness 错误（数据加载路径 type bug，
`source_root` 未转 `Path`）。**T12 / W2 数据接线落地前该变体的验证会停在
这一段**；修好后 exit code 会是 0 或 1（取决于真实 Sharpe）。

### 9.2 读 verdict.json（依赖 9.1 写出）

```bash
$ /Users/mark/sdk/mamba-envs/trading/bin/python3 -c \
    "import json; r=json.load(open('strategies/pairs_cointegration_1d_20260709/results/validation/verdict.json')); print(r['pipeline'], r['verdict'], sorted(r.keys()))"
Traceback (most recent call last):
  File "<string>", line 1, in <module>
FileNotFoundError: [Errno 2] No such file or directory: \
    'strategies/pairs_cointegration_1d_20260709/results/validation/verdict.json'
$ echo $?
1
```

exit code = **1** — verdict.json 不存在（9.1 退出码 2 短路，没写出）。
**正常生产路径下**（harness exit 0 或 1），该命令会打印
`<pipeline> <verdict> [<key1>, <key2>, ...]`，例如：

```
generic FAIL ['fee_shock', 'framework_skips', 'full_native', 'gates',
              'pipeline', 'symbols', 'timeframe', 'variant', 'verdict',
              'windows']
```

其中 `fee_shock` 与 `framework_skips` 是 T8 + 框架 replay 落地后才会出现
的字段。legacy 路径（`strategy.py`）下 `pipeline` 为 `None`，其余结构兼容。

### 9.3 Scaffold 命令冒烟（脚手架落地情况）

W3-T10 计划产出 `scripts/new_variant.py`。**截至 2026-07-25 该文件尚未合并**，
按 §3.2 走手动三步；本节步骤跳过实测。

兜底可用入口（已落地、实测 exit 0）：

```bash
$ /Users/mark/sdk/mamba-envs/trading/bin/python3 \
    -m _shared.templates.scaffold onboard_doc_smoke \
    --symbols BTCUSDT --tf 1h --out-root /tmp/onboard_smoke_root
/private/tmp/onboard_smoke_root/onboard_doc_smoke
$ echo $?
0
$ ls /tmp/onboard_smoke_root/onboard_doc_smoke/
README.md
SPEC.md
config.json
results
strategy.py
tests
# 清理
$ rm -rf /tmp/onboard_smoke_root
```

T10 `scripts/new_variant.py` 落地后，`9.3` 的命令替换为：

```bash
cd /Users/mark/multica/quant-loop && \
    $PY scripts/new_variant.py onboard_doc_smoke \
        --timeframe 1h --symbols BTCUSDT
```

---

## 10. 门禁生效位置（CI）

`ci/validate_changed_variants.sh` 会在每个改动的 variant 目录上跑
`$PY -m validation.oos_harness --variant <dir>`；非零 exit 阻断 merge。
所以 **harness exit 2（harness 错误）同样阻断 PR**——不要把 exit 2 误判为
"我代码没错，是 harness 的 bug"。如果数据加载失败、signals.py 抛异常、
框架引擎崩溃，先修本地数据接线 / signals.py 再说。

---

## 附：本页事实清单

- 门禁阈值：§7，权威源 `validation/gates.py:8-17`。
- exit code 语义：§2，权威源 `validation/oos_harness.py:14-17`。
- contract v2 函数签名与 Trade 形状：§4，权威源
  `_shared/templates/strategy_contract_v2.py:19-33` +
  `_shared/run_backtest.py:77-87`。
- verdict.json 结构：§5 + §6，权威源
  `validation/generic_harness.py:265-272, 301-308, 349` +
  `validation/oos_harness.py:79-80`。
- fee shock：§6，权威源 `_shared/validation/fee_shock.py::fee_shock_sweep`。
- timeframe → freq_per_year：§8 坑 #4，权威源
  `validation/generic_harness.py:62-72`。
- sys.path 兜底：§8 坑 #3，权威源
  `validation/adapters/native_engine.py:47-51`。