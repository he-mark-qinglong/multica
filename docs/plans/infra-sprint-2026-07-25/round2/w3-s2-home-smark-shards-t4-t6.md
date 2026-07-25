# W3-S2 — `/home/smark` 迁移 shards T4-T6 执行卡（scripts/ live_data/ research/）

> 生成：2026-07-25，round-2 planning agent（只读核实，未改任何代码）。
> 三张卡均已在 Mac 本机实读核实：文件清单、行号、改写前后形态全部内联，执行 agent 不需要读任何其他文档。
> 执行机器：全部 **mac**（编辑 + grep + py_compile + bash -n 均在本机完成；不需要网络、不需要数据、不需要跑回测）。

---

## 0. 共享上下文（三张卡通用，执行前必读）

### 0.1 背景

`quant-loop/` 下有 537 处硬编码 `/home/smark/...` 路径（238 文件）。`/home/smark` 在 Mac 上不存在，这些脚本只能在 server-105 跑。本 slice 负责其中三个目录的迁移：`scripts/`（T4）、`live_data/`（T5）、`research/`（T6）。其余目录由其他 slice 负责，**不要越界改它们的文件**。

### 0.2 依赖：`_shared/paths.py`（任务 W3-T1，另一 slice 创建）

本 slice 的三张卡**只 import，不创建** `_shared/paths.py`。它的 API 契约（若执行时已存在但函数名不同，以实际文件为准并相应调整 import 名）：

```python
# quant-loop/_shared/paths.py（W3-T1 创建，本 slice 禁写此文件）
def quant_loop_root() -> Path: ...  # 读 env QUANT_LOOP_ROOT；未设置时从 paths.py 的 __file__ 推导（parents[1] = quant-loop/）
def data_root() -> Path: ...        # quant_loop_root() / "data"
def live_data_root() -> Path: ...   # quant_loop_root() / "live_data"
```

**注意**：`py_compile` 和 `bash -n` 不执行 import，所以即使 T1 尚未落地，本 slice 的验收也能通过。运行期正确性依赖 T1，已在依赖栏声明。

### 0.3 import 引导片段（双模式，照抄 `_shared/execution/cost_model.py:36-52` 的模式）

脚本以 `python3 scripts/foo.py` 方式运行时 `sys.path[0]` 是脚本所在目录，`import _shared` 会失败，因此每个改过的 .py 在既有 import 块之后插入如下引导（**只 import 实际用到的函数**，保持最小）：

文件在 `quant-loop/scripts/` 或 `quant-loop/live_data/`（深度 = parents[1]）：

```python
try:
    from _shared.paths import data_root  # 按需: live_data_root, quant_loop_root
except ImportError:  # 以脚本方式直接运行时补 sys.path
    import sys
    from pathlib import Path
    _QL = str(Path(__file__).resolve().parents[1])  # quant-loop/
    if _QL not in sys.path:
        sys.path.insert(0, _QL)
    from _shared.paths import data_root
```

文件在 `quant-loop/research/<subdir>/`（深度 = parents[2]）：把 `parents[1]` 改成 `parents[2]`，其余相同。

### 0.4 替换映射表（机械执行）

| 原形态 | 替换为 |
|---|---|
| `Path("/home/smark/multica/quant-loop/data")` 或 `"/home/smark/multica/quant-loop/data"` | `data_root()`（原为 str 字面量则 `str(data_root() / ...)`） |
| `"/home/smark/multica/quant-loop/data/<sub>"` | `data_root() / "<sub>"`（保持原类型：原来是 `Path(...)` 就直接给 Path 表达式；原来是 str 就包 `str(...)`） |
| `Path("/home/smark/multica/quant-loop/live_data")` | `live_data_root()` |
| `"/home/smark/multica/quant-loop/strategies/<name>"` | `quant_loop_root() / "strategies" / "<name>"` |
| `sys.path.insert(0, '/home/smark/multica/quant-loop')` | 删除该行，换成 §0.3 的 try/except 引导 |
| `Path('/home/smark/multica/quant-loop/research/<subdir>')`（= 文件自己所在目录） | `Path(__file__).resolve().parent`（更简且更正确，无需 paths.py） |
| docstring / help 文本里的 `/home/smark/multica/quant-loop/...` | 改成相对路径写法 `quant-loop/...`（验收 grep 必须归零，文本也不能留） |

### 0.5 铁律（违反即返工）

1. 只改本卡列出的文件；`.md` / `.json` / `.log` / `.parquet` / `__pycache__/` **一律不碰**（历史证据与数据）。
2. 不改任何 `framework_adapter_*.py`（本 slice 目录内没有，但全局规则如此）。
3. 禁止新写任何路径推导逻辑替代 `_shared/paths.py`（§0.3 的 sys.path 引导是唯一例外，且必须照抄）。
4. 禁止把路径换成 `/Users/mark/...`——目标是双机可跑，不是把硬编码换个家。
5. 除路径行外不改任何逻辑；不格式化、不顺手重构、不改成本常量（如 `research/validation/test_minimal_repro.py:30` 的 `0.0024` 属 W3-T12 范围，**不动**）。
6. 工作区有他人的未提交改动，只对清单内文件做最小编辑。

---

## 卡 W3-T4 — shard: scripts/（9 个 .py + 3 个 .sh）

- **目标**：`quant-loop/scripts/` 下 `/home/smark` 出现次数清零，全部改走 `_shared/paths.py`。
- **机器**：mac ｜ **估时**：20 min ｜ **依赖**：W3-T1（`_shared/paths.py`，仅运行期依赖）
- **读/写文件**（全部已实读核实存在）：

| 文件 | 行 | 现状 | 改写 |
|---|---|---|---|
| `scripts/fetch_binance_spot_1h.py` | 43 | `DEFAULT_OUT_DIR = "/home/smark/multica/quant-loop/live_data"` | `DEFAULT_OUT_DIR = str(live_data_root())` |
| `scripts/fetch_binance_funding.py` | 19 | `DEFAULT_OUT_DIR = "/home/smark/multica/quant-loop/data/funding"` | `DEFAULT_OUT_DIR = str(data_root() / "funding")` |
| `scripts/fetch_binance_usdm_1m.py` | 44 | `DEFAULT_OUT_DIR = "/home/smark/multica/quant-loop/data/perp_1m"` | `DEFAULT_OUT_DIR = str(data_root() / "perp_1m")` |
| `scripts/fetch_binance_usdm_30m.py` | 29 | `DEFAULT_OUT_DIR = "/home/smark/multica/quant-loop/data/perp_30m"` | `DEFAULT_OUT_DIR = str(data_root() / "perp_30m")` |
| `scripts/finalize_aggtrades_report.py` | 25 | `TRADES_DIR = Path("/home/smark/multica/quant-loop/data/trades")` | `TRADES_DIR = data_root() / "trades"` |
| `scripts/backfill_aggtrades_vision.py` | 33 | docstring usage 示例里 `--out-dir /home/smark/.../data/trades` | 改为 `--out-dir data/trades`（相对写法） |
| 同上 | 259 | `ap.add_argument("--out-dir", default="/home/smark/multica/quant-loop/data/trades")` | `default=str(data_root() / "trades")` |
| `scripts/v10_backtrader.py` | 18 | `DATA_DIR = Path("/home/smark/multica/quant-loop/data/perp_30m")` | `DATA_DIR = data_root() / "perp_30m"` |
| 同上 | 19 | `RESULTS_DIR = Path("/home/smark/.../strategies/vpvr_xs_pairs_30m_funding_filter_btc_sol_v10_backtrader_20260717")` | `RESULTS_DIR = quant_loop_root() / "strategies" / "vpvr_xs_pairs_30m_funding_filter_btc_sol_v10_backtrader_20260717"`（**注意：该策略目录已不存在**，只移植路径，目录名原样保留，不要"顺手"改名字） |
| `scripts/v10_grid_search.py` | 22-23 | `V7_DIR` / `WORK_DIR` 两个 `Path("/home/smark/.../strategies/...")` | 同上模式：`quant_loop_root() / "strategies" / "<原名>"`（原名 `vpvr_xs_pairs_30m_funding_filter_btc_sol_regularized_20260712` / `..._v10_optimize_20260717`，均已不存在，照原样保留） |
| `scripts/v10_grid_v2.py` | 7-8 | 同上两个常量 | 同 v10_grid_search 处理 |
| `scripts/run_aggtrades_full_history.sh` | 6-8 | `LOG=` / `PY=` / `OUT=` 三个 `/home/smark/...` 赋值 | 见下方 .sh 规范（无 FINALIZE 行） |
| `scripts/run_aggtrades_full_history_v2.sh` | 10-12 | 同上 | 同 .sh 规范 |
| `scripts/run_aggtrades_full_history_v3.sh` | 11-14 | `LOG` / `PY` / `FINALIZE` / `OUT` 四个赋值 | 同 .sh 规范（含 FINALIZE） |

- **.py 步骤**（9 个文件，每个同样三步）：
  1. 在既有 import 块后插入 §0.3 引导片段（深度 `parents[1]`），只 import 该文件用到的函数（`finalize_aggtrades_report.py` 用 `data_root`；`fetch_binance_spot_1h.py` 用 `live_data_root`；v10 三个文件用 `data_root, quant_loop_root` 等）。
  2. 按上表逐行替换。**保持原类型**：原来是 str 字面量的给 `str(...)`，原来是 `Path(...)` 的给 Path 表达式（`data_root()` 本身返回 Path）。
  3. `python3 -m py_compile <file>` 通过再改下一个。
- **.sh 步骤**（3 个文件）：在顶部 `set -u`（v3 在第 10 行；v1/v2 位置类似，以实际为准）之后插入一行根解析，然后改写赋值。以 v3 为例，11-14 行变为：

  ```bash
  QL_ROOT="${QUANT_LOOP_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
  LOG="$QL_ROOT/data/trades/backfill_run.log"
  PY="$QL_ROOT/scripts/backfill_aggtrades_vision.py"
  FINALIZE="$QL_ROOT/scripts/finalize_aggtrades_report.py"
  OUT="$QL_ROOT/data/trades"
  ```

  v1/v2 无 `FINALIZE` 行，其余相同。文件其余行（`find "$OUT" ...`、`python3 "$PY" ...` 等）一概不动。
- **验收**（全部在 `quant-loop/` 下执行）：

  ```bash
  grep -rn '/home/smark' scripts --include='*.py' --include='*.sh' | wc -l   # 期望: 0
  for f in scripts/*.py; do /Users/mark/sdk/mamba-envs/trading/bin/python3 -m py_compile "$f" || echo "FAIL $f"; done   # 期望: 无 FAIL
  bash -n scripts/run_aggtrades_full_history.sh scripts/run_aggtrades_full_history_v2.sh scripts/run_aggtrades_full_history_v3.sh   # 期望: 无输出
  # 若 _shared/paths.py 已存在（T1 已落地），加做 import 冒烟：
  /Users/mark/sdk/mamba-envs/trading/bin/python3 -c "
  import sys; sys.path.insert(0, 'scripts')
  import fetch_binance_usdm_1m as m
  assert m.DEFAULT_OUT_DIR.endswith('quant-loop/data/perp_1m'), m.DEFAULT_OUT_DIR"
  ```

- **单文件属主**：整个 `scripts/` 目录归本卡，无并行冲突。

---

## 卡 W3-T5 — shard: live_data/（6 个 .py）

- **目标**：`quant-loop/live_data/` 下 `/home/smark` 清零。
- **机器**：mac ｜ **估时**：10 min ｜ **依赖**：W3-T1（仅运行期）
- **读/写文件**（全部已实读核实）：

| 文件 | 行 | 现状 | 改写 |
|---|---|---|---|
| `live_data/verify_sma34872.py` | 24 | `OUT_DIR = Path("/home/smark/multica/quant-loop/live_data")` | `OUT_DIR = live_data_root()` |
| `live_data/refresh_klines_sma34871.py` | 31 | `LIVE_DATA = Path("/home/smark/multica/quant-loop/live_data")` | `LIVE_DATA = live_data_root()` |
| 同上 | 32 | `PERP_1M = Path("/home/smark/multica/quant-loop/data/perp_1m")` | `PERP_1M = data_root() / "perp_1m"` |
| `live_data/verify_sma34898.py` | 29-30 | 同 refresh 两行（`LIVE_DATA` / `PERP_1M`） | 同上 |
| `live_data/fetch_binance_usdt_15m.py` | 5 | docstring 内 ``` ``/home/smark/multica/quant-loop/live_data/{SYMBOL}USDT_15m.parquet`` ``` | 改为 ``` ``live_data/{SYMBOL}USDT_15m.parquet`` ``` |
| 同上 | 48 | `OUTPUT_DIR = Path("/home/smark/multica/quant-loop/live_data")` | `OUTPUT_DIR = live_data_root()` |
| `live_data/fetch_binance_usdm_4h.py` | 156 | `ap.add_argument("--out-dir", default="/home/smark/multica/quant-loop/live_data", ...)` | `default=str(live_data_root())`（157 行 help 文本无 /home/smark，不动） |
| `live_data/verify_usdt_15m.py` | 17 | `OUTPUT_DIR = Path("/home/smark/multica/quant-loop/live_data")` | `OUTPUT_DIR = live_data_root()` |

- **步骤**：每个文件插入 §0.3 引导（深度 `parents[1]`；`refresh_klines_sma34871.py` 和 `verify_sma34898.py` 需同时 import `data_root, live_data_root`）→ 按表替换 → 逐个 py_compile。目录内 `.parquet` / `.json` / `.md` 全部不碰。
- **验收**：

  ```bash
  grep -rn '/home/smark' live_data --include='*.py' --include='*.sh' | wc -l   # 期望: 0
  for f in live_data/*.py; do /Users/mark/sdk/mamba-envs/trading/bin/python3 -m py_compile "$f" || echo "FAIL $f"; done   # 期望: 无 FAIL
  ```

- **单文件属主**：整个 `live_data/` 归本卡。

---

## 卡 W3-T6 — shard: research/（13 个 .py）

- **目标**：`quant-loop/research/` 下全部非 graveyard .py 的 `/home/smark` 清零（共 22 处，分布已逐文件核实）。
- **机器**：mac ｜ **估时**：20 min ｜ **依赖**：W3-T1（仅运行期）
- **深度注意**：本卡所有文件在 `research/<subdir>/`，§0.3 引导用 **`parents[2]`**。
- **简化规则**：本卡大量 `OUT`/`BASE` 常量就是文件自己所在目录，一律用 `Path(__file__).resolve().parent`（不必 import paths.py）。
- **读/写文件**：

**research/ofi/（8 个文件，14 处）**

| 文件 | 行 | 现状 | 改写 |
|---|---|---|---|
| `01_load_test.py` | 9 | `ROOT = '/home/smark/multica/quant-loop/data/trades/BTCUSDT_aggtrades.parquet/year=2026/month=4'` | `ROOT = str(data_root() / 'trades' / 'BTCUSDT_aggtrades.parquet' / 'year=2026' / 'month=4')` |
| `01_load_test_v2.py` | 14 | `ROOT = '/home/smark/.../data/trades/BTCUSDT_aggtrades.parquet'` | `ROOT = str(data_root() / 'trades' / 'BTCUSDT_aggtrades.parquet')` |
| 同上 | 62 | `bars.to_parquet('/home/smark/.../research/ofi/btc_1m_3mo.parquet')` | `bars.to_parquet(Path(__file__).resolve().parent / 'btc_1m_3mo.parquet')` |
| `02_ofi_signal.py` | 36 | `sys.path.insert(0, '/home/smark/multica/quant-loop')`（为 import `_shared`） | 删除该行；把 35-37 行整体换成 §0.3 引导（parents[2]，import 该文件用到的函数即可，不必 import paths 的三个 root——此文件的 sys.path.insert 原目的就是 `_shared` 可达） |
| 同上 | 39 | `OUT = Path('/home/smark/multica/quant-loop/research/ofi')` | `OUT = Path(__file__).resolve().parent` |
| `03_signed_check.py` | 8 | `sys.path.insert(0, '/home/smark/multica/quant-loop')` | 同 02 的处理（换 §0.3 引导） |
| `04_simple_long.py` | 5 | 同上 | 同上 |
| `05_net_backtest.py` | 9 | `sys.path.insert(0, '/home/smark/multica/quant-loop')` | 同上 |
| 同上 | 13 | `OUT = '/home/smark/multica/quant-loop/research/ofi'`（str） | `OUT = str(Path(__file__).resolve().parent)` |
| `06_summary.py` | 5-6 | 两行 insert：`.../research/ofi` 和 `.../quant-loop` | 第一行换 `sys.path.insert(0, str(Path(__file__).resolve().parent))`（若原样保留语义需要）；第二行换 §0.3 引导。若下文未从 sibling import，可直接删第一行——以文件实际 import 为准，改后 py_compile + grep 复核 |
| 同上 | 115 | `with open('/home/smark/.../research/ofi/verdict.json', 'w')` | `with open(Path(__file__).resolve().parent / 'verdict.json', 'w')` |
| `ofi_sanity.py` | 5 | `OUT = Path('/home/smark/multica/quant-loop/research/ofi')` | `OUT = Path(__file__).resolve().parent` |

**research/calibration/（3 个文件，5 处）**

| 文件 | 行 | 现状 | 改写 |
|---|---|---|---|
| `compute_inhouse.py` | 21 | `SRC = Path(".../research/calibration/BTCUSDT__30m.parquet")` | `SRC = Path(__file__).resolve().parent / 'BTCUSDT__30m.parquet'` |
| 同上 | 22 | `OUT = Path(".../research/calibration/inhouse_buyhold_2024.json")` | `OUT = Path(__file__).resolve().parent / 'inhouse_buyhold_2024.json'` |
| `compute_framework.py` | 30-31 | 同上形态（`framework_buyhold_2024.json`） | 同 compute_inhouse |
| `compare_and_report.py` | 6 | `BASE = Path("/home/smark/multica/quant-loop/research/calibration")` | `BASE = Path(__file__).resolve().parent` |

**research/validation/（1 个文件，1 处）**

| 文件 | 行 | 现状 | 改写 |
|---|---|---|---|
| `test_minimal_repro.py` | 26-29 | `DEFAULT_STRATEGY_DIR = Path("/home/smark/multica/quant-loop/strategies/" "vpvr_xs_pairs_30m_funding_filter_20260712")` | 插 §0.3 引导（import `quant_loop_root`），改为 `DEFAULT_STRATEGY_DIR = quant_loop_root() / "strategies" / "vpvr_xs_pairs_30m_funding_filter_20260712"`。**该策略目录已不存在**，目录名原样保留。`:30` 的 `PAIR_ROUND_TRIP_COST = 0.0024` **不动**（W3-T12 范围） |

**research/spillover_2026-07-19/（1 个文件，1 处 —— 特例）**

| 文件 | 行 | 现状 | 改写 |
|---|---|---|---|
| `feasibility_check.py` | 7 | `DATA_DIR = '/home/smark/services/strategy_display_engine_data/canonical/workdir/strategies/vpvr_reversion_1m_nostop_20260630/data'` | **该路径在 quant-loop 仓库之外**，`_shared/paths.py` 不覆盖。改为 env 驱动（`os` 已在第 2 行 import）：<br>`DATA_DIR = os.environ.get("SPILLOVER_DATA_DIR")`<br>`if not DATA_DIR:`<br>`    raise SystemExit("Set SPILLOVER_DATA_DIR to the strategy_display_engine canonical data dir (was /home/smark/services/...; not in this repo).")` |

- **步骤**：逐文件插引导（parents[2]）→ 按表替换 → py_compile → 下一个。`research/` 下的 `.md` / `.json` / `.tsv` / `.sh`（`archive_trading_repo.sh` 等无 /home/smark）/ `JOURNAL.md` 全部不碰。
- **验收**：

  ```bash
  grep -rn '/home/smark' research --include='*.py' --include='*.sh' | wc -l   # 期望: 0
  for f in research/ofi/*.py research/calibration/*.py research/validation/*.py research/spillover_2026-07-19/*.py; do /Users/mark/sdk/mamba-envs/trading/bin/python3 -m py_compile "$f" || echo "FAIL $f"; done   # 期望: 无 FAIL
  ```

- **单文件属主**：整个 `research/` 归本卡。

---

## 依赖与冲突（给 parent）

- **依赖**：三卡均依赖 **W3-T1**（`_shared/paths.py` 的 `quant_loop_root` / `data_root` / `live_data_root` 契约，见 §0.2）。这是跨 slice 的**接口契约风险**：若 T1 实现的函数名/返回类型与本卡 §0.2 不符，三卡全部要返工。建议 T1 的验收里把这三个函数签名钉死。
- **与其他 slice 的交叠**：无文件级冲突——本 slice 只碰 `scripts/`、`live_data/`、`research/`；T7/T8（strategies/）、T9（backtest/ 等顶层）目录不相交。
- **发现并上报的异常**（不阻塞，不需本 slice 处理）：
  1. `scripts/v10_backtrader.py` / `v10_grid_search.py` / `v10_grid_v2.py` / `research/validation/test_minimal_repro.py` 引用的 4 个 `strategies/vpvr_xs_pairs_30m_funding_filter*` 目录**在仓库里已不存在**（不在 strategies/ 也不在 _graveyard/，已 `ls` 核实）。迁移只让路径可移植，运行时仍会 FileNotFound。建议 parent 考虑把这 4 个脚本列入归档/删除议题（它们属已被 KILL 的 vpvr_xs_pairs_30m 家族）。
  2. `research/spillover_2026-07-19/feasibility_check.py:7` 的 `/home/smark/services/...` 是仓库外路径，env 化后该脚本在无常量默认时直接报错退出——这是有意的（数据本就不在仓内），但意味着它无法零配置运行。
  3. `research/validation/test_minimal_repro.py:30` 有内联成本字面量 `0.0024`，会被 W3-T12 扫描器命中——属 T12/T14 处理范围，本卡不改。
  4. `scripts/run_aggtrades_full_history*.sh` 里 `df -BG --output=avail` 是 GNU 语法，在 mac 上跑不了——但这是既有行为，且这些脚本本就面向 server-105 执行，不在本迁移范围内改动。
