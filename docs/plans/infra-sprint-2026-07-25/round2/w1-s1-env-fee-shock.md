# W1-S1 — T1/T2 执行卡：trading env 引擎安装 + `_shared/validation/fee_shock.py`

> 细化自 round1 `w1-backtest-engine.md` 的 T1、T2。本文件自包含，执行 agent 无需读 round1。
> Python 一律 `/Users/mark/sdk/mamba-envs/trading/bin/python3`（下称 `$PY`）。
> 所有 pytest 命令的工作目录均为 `/Users/mark/multica/quant-loop`。

## 已核实的现场事实（planning agent 实测，2026-07-25）

- `$PY` = Python **3.10.20**（conda-forge, macOS **x86_64**），pip 24.2。不是 py3.12 —— freqtrade 兼容性风险比 round1 预估的低。
- 三个引擎当前**全部缺失**（实测）：`import backtrader / freqtrade / vectorbt` 均 ModuleNotFoundError。
- 版本声明在 `quant-loop/validation/requirements.txt:6-8`：`backtrader>=1.9.78`、`freqtrade>=2024.9`、`vectorbt>=0.26`（vectorbt 那行被注释为 optional，但 G5/vectorbt leg 需要它，照装）。
- PyPI 实测可用版本：backtrader 最新 `1.9.78.123`；vectorbt 0.x 最新 `0.28.5`（**已有 1.0.0，API 不兼容风险，必须钉 <1.0**）。
- TA-Lib C 库未安装（`ls /usr/local/lib/libta*` 无结果）—— 这是 freqtrade pip 安装最可能的失败点。
- 基线测试当前绿：`cd /Users/mark/multica/quant-loop && $PY -m pytest validation/ _shared/ -q` → 142 passed, 3 skipped（round1 实测）。
- 参考实现存在：`quant-loop/research/swarm/2026-07-25/H3-variants-h1h2h4/run_btcsol_variants_fixed.py:313-346` `fee_shock_metrics`（daily 重采样 + 按日 drag，已读码核实逻辑见 T2 卡内联）。
- `_shared/validation/` 现有文件：`compute_metrics.py`、`cpcv.py`、`test_compute_metrics.py`、`test_cpcv.py`（**无 `__init__.py`**，靠 PEP 420 namespace package + pytest rootdir 导入，新模块沿用同模式，**不要**新建 `__init__.py`）。
- 测试风格基准：`_shared/validation/test_compute_metrics.py`（`from _shared.validation.compute_metrics import ...`、helper 构造函数、docstring 说明意图）。

---

## T1 — 安装 backtrader / vectorbt / freqtrade 到 trading mamba env

- **目标**：让三个框架引擎在本机可 import，打通双框架 CV 的物理前提。
- **machine**：**mac**（env 在 `/Users/mark/sdk/mamba-envs/trading/`，105 上无此 env）。
- **est**：20 min（freqtrade 失败降级路径 +5 min）。
- **deps**：无。**⚠ 全 sprint 唯一允许动 mamba env 的任务**；若其他 workstream 有 pip/mamba 变更需求，必须与本任务串行。

### 触及文件

- 读：`/Users/mark/multica/quant-loop/validation/requirements.txt`（仅核对版本下界）。
- 写：**无仓库文件**。仅 `$PY -m pip install`（改 env，不改 repo）。**禁止**修改 requirements.txt、禁止 `pip install -r` 整文件（会动 pandas/numpy/pyarrow 等已固定版本）。

### 步骤

1. 设别名：`PY=/Users/mark/sdk/mamba-envs/trading/bin/python3`。先确认现状：`$PY -c "import backtrader" 2>&1 | tail -1`（预期 ModuleNotFoundError）。
2. 装 backtrader（纯 Python，必成功）：
   ```bash
   $PY -m pip install "backtrader==1.9.78.123"
   ```
3. 装 vectorbt（钉 0.x，避开 1.0.0 API 断裂；会拉 numba/llvmlite，x86_64 py3.10 有 wheel）：
   ```bash
   $PY -m pip install "vectorbt==0.28.5"
   ```
   若 numba/llvmlite 编译失败，重试一次 `$PY -m pip install --only-binary :all: "vectorbt==0.28.5"`；仍失败则记录为部分失败并继续第 4 步（vectorbt 是 informational leg，可缺）。
4. 装 freqtrade（**最可能失败**，失败点是依赖 `ta-lib` python 包需要 TA-Lib C 库）：
   ```bash
   $PY -m pip install "freqtrade==2024.9"
   ```
   - 若报 ta-lib 相关错误（`ta_lib`/`TA-Lib`/`talib` 字样）：执行 `brew install ta-lib`，然后重试同一条 pip 命令。brew 装 C 库属于本任务授权的 env 变更范围。
   - 若 brew 不可用或重试仍失败：**降级为双框架 = backtrader + vectorbt**，在任务结果里如实写明 "freqtrade NOT installed, G5 降级为 backtrader+vectorbt 双框架"。不要耗超过 10 分钟。
   - 版本下界 `>=2024.9`：若 2024.9 解析失败，可试 `freqtrade==2024.12`，仍失败走降级。
5. **验证未破坏现有依赖**（关键，防止 pip 顺手升降 numpy/pandas）：
   ```bash
   $PY -c "import numpy, pandas, pyarrow, scipy; print(numpy.__version__, pandas.__version__)"
   cd /Users/mark/multica/quant-loop && $PY -m pytest validation/ _shared/ -q
   ```
   若 pytest 从 142 passed 变红，用 `$PY -m pip freeze` 对比定位被改动的包并恢复（`$PY -m pip install "numpy==<原版本>"`）。
6. 记录最终状态（写进任务结果）：
   ```bash
   $PY -m pip freeze | grep -iE "^(backtrader|vectorbt|freqtrade|ta-lib|numba|llvmlite)" 
   ```

### 验收（全部要过，或文档化的部分成功）

```bash
/Users/mark/sdk/mamba-envs/trading/bin/python3 -c "import backtrader, vectorbt; print('ok')"
# 预期输出: ok，exit 0
/Users/mark/sdk/mamba-envs/trading/bin/python3 -c "import freqtrade; print('freqtrade ok')"
# 预期: exit 0；若降级路径，此条可 FAIL 但结果文本必须明确记录
cd /Users/mark/multica/quant-loop && /Users/mark/sdk/mamba-envs/trading/bin/python3 -m pytest validation/ _shared/ -q
# 预期: 142 passed, 3 skipped（与基线一致，不允许变少）
```

---

## T2 — 新建 `_shared/validation/fee_shock.py` + 测试

- **目标**：把 H3-variants runner 的 fee shock 逻辑提炼为 `_shared` 纯函数模块，供后续 generic_harness 接入（下游 T8 会 import 本模块，签名必须与本卡一致）。
- **machine**：**mac**（pytest 基线只能在此 env 跑）。
- **est**：25 min。
- **deps**：无（与 T1 文件集不相交，可并行）。

### 触及文件

- 读（参考实现，逻辑已内联下方，无需再读）：`/Users/mark/multica/quant-loop/research/swarm/2026-07-25/H3-variants-h1h2h4/run_btcsol_variants_fixed.py:313-346`
- 读（风格基准）：`/Users/mark/multica/quant-loop/_shared/validation/compute_metrics.py`、`/Users/mark/multica/quant-loop/_shared/validation/test_compute_metrics.py`
- **写（新建，仅此两个文件）**：
  - `/Users/mark/multica/quant-loop/_shared/validation/fee_shock.py`
  - `/Users/mark/multica/quant-loop/_shared/validation/test_fee_shock.py`

### 参考实现原文语义（run_btcsol_variants_fixed.py:313-346，提炼依据）

对 bar 级 equity：`equity.resample("1D").last()` → 日收益；trades 按 `exit_ts` floor 到日计数，每日 drag = `n_trades_that_day * (rt_bps/10_000) * per_trade_fraction`；`adj_ret = daily_ret - drag`；`adj_eq = (1+adj_ret).cumprod() * daily_eq.iloc[0]`；输出 sharpe（日频 × √365，std ddof=1，<2 样本或 std<1e-12 → 0.0）、annualized（按实际 span 年数复利）、total_return、max_dd（负值约定）。tz-aware exit_ts 先 `tz_convert(None)`。

### 模块规格（签名是下游 T8 的契约，**一字不改**）

```python
# _shared/validation/fee_shock.py
"""Shared fee-shock replay: stress a gross equity curve with extra round-trip cost.

Extracted from research/swarm/2026-07-25/H3-variants-h1h2h4/run_btcsol_variants_fixed.py
(fee_shock_metrics, lines 313-346) as the single authoritative implementation.
Method: daily-resample equity, subtract per-exit-day cost drag, replay, report metrics.
"""
from __future__ import annotations

import math
import pandas as pd


def fee_shock_metrics(equity, trades, extra_rt_bps, per_trade_fraction=0.005):
    """Replay `equity` (bar-level pd.Series, datetime index) with an additional
    round-trip cost of `extra_rt_bps` bps charged on `per_trade_fraction` of equity
    per trade, debited on each trade's exit day.

    trades: iterable of dicts with an "exit_ts" key (tz-aware or naive).
    Returns dict keys: extra_round_trip_bps, sharpe_daily_resampled,
    annualized_return, total_return, max_drawdown_pct (negative convention).
    extra_rt_bps=0.0 must reproduce the un-shocked curve's metrics.
    """
    # ... 按上面"参考实现原文语义"逐行移植,唯一命名差异:
    #     返回 key "pair_round_trip_bps" 改名为 "extra_round_trip_bps",
    #     参数 pair_rt_bps 改名为 extra_rt_bps。


def fee_shock_sweep(equity, trades, bps_list, per_trade_fraction=0.005):
    """Run fee_shock_metrics for each level in bps_list.
    Returns {str(float(bps)): metrics_dict} — e.g. key "60.0" for 60.0.
    (T8 会以 report["fee_shock"]["60.0"]["sharpe_daily_resampled"] 取值,键格式固定。)"""
    return {str(float(b)): fee_shock_metrics(equity, trades, b, per_trade_fraction)
            for b in bps_list}
```

实现注意点（照参考实现搬运即可）：

- `trades` 判空用 `if trades:`（参考实现语义：空 list/None 都无 drag）。
- `exit_dates.tz` 与 `counts.index.tz` 两处 tz 检查都要保留（tz-aware 与 naive 输入都能过）。
- span 计算：`(adj_eq.index[-1] - adj_eq.index[0]).total_seconds() / (365.25 * 24 * 3600)`；`span > 0` 才算 annualized，否则 0.0。
- 单日 equity（resample 后只有 1 个点）不得崩：total=0、sharpe=0、max_dd=0。

### 测试规格（`_shared/validation/test_fee_shock.py`，≥6 个用例）

导入方式与 test_compute_metrics.py 一致：`from _shared.validation.fee_shock import fee_shock_metrics, fee_shock_sweep`。

1. **`test_zero_shock_reproduces_baseline`**：构造 30 天日级 equity（`pd.date_range(..., freq="D")`，逐日 +0.1%），若干 trades；`extra_rt_bps=0.0` 时输出的 `total_return` 必须等于直接对同一 equity 手算的 `(eq.iloc[-1]/eq.iloc[0]-1)`（`math.isclose`, rel_tol=1e-12），`sharpe_daily_resampled` 等于 `daily_ret.mean()/daily_ret.std(ddof=1)*sqrt(365)` 的手算值。
2. **`test_known_two_trade_vector_exact`**（手算精确匹配，核心用例）：3 天日级 equity `[100.0, 110.0, 88.0]`（index = 3 个连续日）。daily_ret = `[0.0, 0.1, -0.2]`。trades = 2 个 dict，`exit_ts` 都在第 3 天。`extra_rt_bps=10_000.0, per_trade_fraction=0.05` → 每 trade drag = `1.0 * 0.05 = 0.05`，第 3 天总 drag = 0.1 → adj_ret 第 3 天 = `-0.2 - 0.1 = -0.3`。预期精确断言（isclose, abs_tol=1e-12）：
   - `total_return == 110.0 * 0.7 / 100.0 - 1.0 == -0.23`
   - `max_drawdown_pct == 77.0/110.0 - 1.0 == -0.30`
   - `sharpe_daily_resampled < fee_shock_metrics(同 equity, 同 trades, 0.0)["sharpe_daily_resampled"]`（shock 严格拉低 sharpe）
3. **`test_empty_trades_no_drag`**：trades=`[]`、extra_rt_bps=60 → 输出与 trades=None 完全一致（逐 key ==）。
4. **`test_tz_aware_exit_ts_handled`**：exit_ts 用 `pd.Timestamp("2026-01-03 12:00", tz="UTC")` 与 naive 同刻时间各跑一遍，两结果逐 key isclose。
5. **`test_single_day_equity_no_crash`**：单日 equity → `total_return == 0.0`、`sharpe_daily_resampled == 0.0`、`max_drawdown_pct == 0.0`。
6. **`test_sweep_keys_and_monotonicity`**：`fee_shock_sweep(eq, trades, [0.0, 30.0, 60.0])` → 键恰好是 `{"0.0", "30.0", "60.0"}`；且 `sharpe` 随 bps 单调不增（`s["0.0"] >= s["30.0"] >= s["60.0"]`）。

### 验收

```bash
cd /Users/mark/multica/quant-loop && /Users/mark/sdk/mamba-envs/trading/bin/python3 -m pytest _shared/validation/test_fee_shock.py -q
# 预期: 6 passed（或更多,0 failed）
cd /Users/mark/multica/quant-loop && /Users/mark/sdk/mamba-envs/trading/bin/python3 -m pytest validation/ _shared/ -q
# 预期: 148 passed, 3 skipped（基线 142 + 新增 6）
grep -c "def fee_shock_metrics" /Users/mark/multica/quant-loop/_shared/validation/fee_shock.py
# 预期: 1
```

---

## Cross-slice notes（给 parent）

- **T1 是唯一 env 变更任务**（round1 §4 warning 1 重申）：w2（server/Go）、w3（data）、w4、w5 若有任何 pip/mamba 安装需求，必须与 T1 串行，不能并行执行。
- T2 的 `fee_shock_metrics` / `fee_shock_sweep` 签名是 W1 下游 T8（generic_harness 接入 fee shock leg）的 import 契约；若 T8 在别的 slice 卡里，其 prompt 里出现的签名必须与本卡一致（含 sweep 返回键格式 `str(float(bps))`，如 `"60.0"`）。
- 本 slice 不碰 `server/`、`strategies/`、requirements.txt；与 w2/w3 无文件级冲突。
- T1 若 freqtrade 降级失败：不影响 T2；影响 W1 的 T9（vectorbt 纳入 G5，反而更关键）与 T12（backtrader leg）——这两条在本 env 上仍可做，因为 backtrader+vectorbt 是必装项。真正受影响的只是"freqtrade 作为第二 CV 框架"这一口径，需 parent 决策是否接受 backtrader+vectorbt 作为双框架组合。
