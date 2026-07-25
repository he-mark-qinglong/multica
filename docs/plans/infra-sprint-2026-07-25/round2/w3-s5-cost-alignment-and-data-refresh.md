# Round-2 执行卡 — w3-s5：成本对齐（T13/T14）+ 数据增量刷新（T15）

> 生成：2026-07-25，round-2 planning agent。所有 file:line 均为本机实读核实。
> 执行体：caocao-m3 cheap agents，零上下文，每个任务 <30min。
> python 一律 `/Users/mark/sdk/mamba-envs/trading/bin/python3`（下写 `$PY`）。
> **禁止**：改 `_graveyard/`、改任何 `results/*.json`/`metrics.json` 结果文件、重跑回测、git 操作、
> 改 `config.json` 里的成本值（结果变更属主线判决权）、碰 `framework_adapter_*.py`。

---

## 0. 排期建议（W4 冻结冲突 —— 本 slice 最重要的输出）

W4（signal-enhance-h3 全历史验证）round-1 §5.1 钉死以下文件**字节级冻结**，其 parity 验收
（W4-T02 断言对齐索引 `len==2448219`、W4-T05/T06 逐位一致、W4-T08 窗口边界断言）直接依赖：

- `quant-loop/strategies/_indicators/mtf_xs_pairs_base_20260718.py` → **撞 T14（base 部分）**
- `quant-loop/strategies/mtf_xs_pairs_1m_15m_2h_h3_20260718/config.json` → **撞 T14（config 部分，本 slice 本来就不许改 config）**
- `quant-loop/data/perp_1m/*.parquet`、`quant-loop/data/funding/*.parquet` → **撞 T15（刷新就是追加 parquet 行，会让 W4 的 2448219 断言假阴性失败）**
- `quant-loop/research/swarm/2026-07-25/H3-variants-h1h2h4/run_btcsol_variants_fixed.py`、`H3-baseline-repro/metrics.json`（本 slice 不碰）

**排期结论**：

| 批次 | 任务 | 前置 |
|---|---|---|
| 批次 1（随时可跑，与 W4 零交叠） | **T13**（run_strategy 默认值）、**T14A**（策略目录迁移笔记 + sizing_sweep fallback） | T14A 软依赖 T12（成本约定文档，可同批） |
| 批次 2（**必须在 W4 全部 wave 完成、冻结解除后**） | **T14B**（base 模块成本常量迁移）→ **T15A**（klines+funding 增量抓取合并）→ **T15B**（resample+manifest+inventory） | T15B 另依赖 W3-T3（inventory 生成器） |

W4 关键路径是 T06 parity → T08 → T09-14 → T15 聚合；base 模块和 data parquet 在 W4 wave 2-5 全程被只读 import/断言，**冻结解除点 = W4-T15（aggregate_verdict）完成**。调度器应以 W4 完成信号放行批次 2，不要按墙钟猜。

另注意（非 W4）：T13 把通用 runner 默认成本 24→22bps，任何依赖旧默认值做历史对比重跑的 compare/ledger 流程口径会变 2bps——已要求 T13 验收测试固定显式值防隐式漂移，但请知会 compare/ledger 工作流 owner。

---

## T13 — run_strategy.py 默认成本 24→22bps（对齐批准值）

- **目标**：通用 runner 的 `cost_bps_rt` 默认值改为从批准的 cost_model 常量推导（22bps RT），不再硬编 24.0。
- **机器**：mac ｜ **估时**：15 min ｜ **依赖**：无（cost_model 已存在；与 W4 冻结零交叠）

**读（核实锚点）**：
- `quant-loop/_shared/templates/run_strategy.py` — L143 `cost_bps_rt: float = 24.0`（函数签名默认值）、L226 `parser.add_argument("--cost-bps-rt", type=float, default=24.0)`、L33-42（现有 sys.path 装配 + import 块）
- `quant-loop/_shared/execution/cost_model.py` — L84-89 `BINANCE_FUTURES`（taker 4.0 + fixed slippage 7.0 /side）、L113-138 `apply_cost()`（RT = 2×(fee+slip) = 22bps，已实测 `$PY -c "from _shared.execution.cost_model import BINANCE_FUTURES; print(2*(BINANCE_FUTURES.taker_fee_bps+BINANCE_FUTURES.fixed_pure_slippage_bps))"` = 22.0）
- `quant-loop/backtest/factor_backtester.py` L53-57 — 批准常量源头（SMA-34900：fee 4.0，slippage = 11−4 = 7.0）
- `quant-loop/_shared/templates/test_strategy_contract_v2.py` L163-181 — 现有测试**显式传** `cost_bps_rt=24.0`（L169），不受默认值变更影响，**不要改它**

**写**：
1. `quant-loop/_shared/templates/run_strategy.py`（仅此一个既有文件）：
   - 在 L42 的 import 块后加 `from _shared.execution.cost_model import BINANCE_FUTURES  # noqa: E402`
   - 新增模块级常量（放在 L45 `_MINUTES_PER_YEAR` 附近）：
     ```python
     #: Ratified perp round-trip cost (SMA-34900): 2 x (4bps fee + 7bps slippage).
     DEFAULT_COST_BPS_RT: float = 2.0 * (
         BINANCE_FUTURES.taker_fee_bps
         + (BINANCE_FUTURES.fixed_pure_slippage_bps or 0.0)
     )
     ```
   - L143 改 `cost_bps_rt: float = DEFAULT_COST_BPS_RT`；L226 改 `default=DEFAULT_COST_BPS_RT` 并给该 argument 加 `help="round-trip cost bps (default: ratified SMA-34900 perp = 22.0, from _shared.execution.cost_model.BINANCE_FUTURES)"`
   - L163-165 docstring 里 `cost_bps_rt` 一行补一句默认来源
2. 新建 `quant-loop/_shared/templates/test_run_strategy_cost.py`：
   - `test_default_cost_is_ratified_22`：`inspect.signature(run_strategy).parameters["cost_bps_rt"].default == 22.0` 且 `== DEFAULT_COST_BPS_RT`（两断言，防常量与签名漂移）
   - `test_cli_default_cost`：`main(["--help"])` 捕获 SystemExit/stdout 或直接用 argparse 解析空参后 `args.cost_bps_rt == 22.0`（可构造 parser 逻辑复用；最简单：subprocess 跑 `--help` grep "22.0"）
   - `test_explicit_cost_still_honored`：调 `run_strategy(..., cost_bps_rt=24.0, bars=合成数据)`（合成 bars 与 example_strategy 用法照抄 `test_strategy_contract_v2.py` L163-181）断言正常返回，证明显式传参路径未被破坏

**验收**：
```bash
cd /Users/mark/multica/quant-loop && $PY -m pytest _shared/templates/test_run_strategy_cost.py _shared/templates/test_strategy_contract_v2.py -q   # 全过
$PY -m _shared.templates.run_strategy --help | grep -c '22\.0'   # >= 1
```

---

## T14A — mtf_xs_pairs 策略目录成本迁移笔记 + sizing_sweep fallback 对齐（不动 base、不动 config）

- **目标**：在 h1-h4 四目录落 `results/cost_migration_note.md`（旧 8bps pair-RT → 批准 44bps pair-RT 的差异记录）；把 h3 `sizing_sweep.py` 的 fallback 默认值 `1.0` 换成 cost_model 批准常量。**不改任何 config.json、不改结果文件、不重跑回测**。
- **机器**：mac ｜ **估时**：20 min ｜ **依赖**：软依赖 T12（COST_CONVENTION.md，笔记中引用其结论；若 T12 未落地则笔记直接引用 cost_model.py:84-89 也行）；**不得**依赖 base 模块改动（那是 T14B）

**背景事实（已核实，写进笔记用）**：
- 旧成本口径：`strategies/_indicators/mtf_xs_pairs_base_20260718.py` L576 `cost = 2.0 * 2.0 * (fee_bps + slip_bps) / 10_000.0`，参数默认 L464 `fee_bps=1.0, slip_bps=1.0`；L812-813 从 cfg 读 `fees_bps_per_side`/`slippage_bps_per_side`（默认也是 1.0）。→ 每笔 pair 交易成本 = 2腿×2边×2bps = **8bps（0.0008）**
- 批准口径：`BINANCE_FUTURES` = 4bps fee + 7bps slip /边/腿（cost_model.py:84-89，SMA-34900）→ 同公式 = 2×2×11 = **44bps（0.0044）/笔**。**两者不等价（差 36bps/笔）**，按规则只记录差异、禁止改结果、交主线判决
- 族内成本字面量分布（grep 实测）：
  - h1/h2 的 `.py`（strategy.py/run_backtest.py/walk_forward.py/data_loader.py）**无内联成本字面量**——成本全走 base 引擎 + config.json 的 1.0/1.0
  - h3：`write_winner_trades.py` L69-70（从 cfg 读，无字面量）、`sizing_sweep.py` L358-359（`.get("fees_bps_per_side", 1.0)` fallback 字面量）、`framework_validate.py` L73-151（`FREQTRADE_FEE_RT_BPS` 等跨框架对比常量——**不改**，这是 framework CV 的比较口径，属另一工作流）
  - h4 目录只有 `results/`（无 .py）
  - 四目录 `config.json`（h3 另有 config_ethbtc/btcsol/ethsol.json）均显式 `fees_bps_per_side: 1.0`——**全部保持原样**（h3 config.json 还在 W4 冻结清单上）

**读**：
- `quant-loop/strategies/mtf_xs_pairs_1m_15m_2h_h3_20260718/sizing_sweep.py` L160-200、L350-410
- `quant-loop/_shared/execution/cost_model.py` L36-52（双模式 sys.path import 模板，照抄）
- 各目录现有 `results/` 内容（ls 确认不覆盖既有文件）

**写**：
1. 新建 4 个文件：`quant-loop/strategies/mtf_xs_pairs_1m_15m_2h_h{1,2,3,4}_20260718/results/cost_migration_note.md`，内容统一模板：旧口径 8bps/笔（base L576 公式 + 参数来源行号）、批准口径 44bps/笔（cost_model.py:84-89）、差值 +36bps/笔、本目录成本字面量清单（h1/h2/h4 写「无内联字面量，成本由 base 引擎 + config.json 1.0/1.0 决定」；h3 列出 sizing_sweep L358-359 与 framework_validate 不改说明）、以及「config 显式值仍为 1.0/1.0，切换批准值属主线判决，本笔记不改行为」
2. 改 `quant-loop/strategies/mtf_xs_pairs_1m_15m_2h_h3_20260718/sizing_sweep.py`（仅此一个既有 .py）：
   - 文件头部照 cost_model.py L36-52 模式加双模式 import：
     ```python
     try:
         from _shared.execution.cost_model import (
             SMA34900_FEE_BPS_PER_SIDE, SMA34900_PURE_SLIPPAGE_BPS_PER_SIDE)
     except ImportError:
         import sys as _sys
         from pathlib import Path as _Path
         _root = str(_Path(__file__).resolve().parents[2])  # quant-loop/
         if _root not in _sys.path:
             _sys.path.insert(0, _root)
         from _shared.execution.cost_model import (
             SMA34900_FEE_BPS_PER_SIDE, SMA34900_PURE_SLIPPAGE_BPS_PER_SIDE)
     ```
     （先 `ls quant-loop/strategies/mtf_xs_pairs_1m_15m_2h_h3_20260718/` 确认 parents[2] 确实是 quant-loop/；若文件已有 sys.path 装配则复用，勿重复）
   - L358 改 `fee_bps = float(cfg.get("fees_bps_per_side", SMA34900_FEE_BPS_PER_SIDE))`；L359 改 `slip_bps = float(cfg.get("slippage_bps_per_side", SMA34900_PURE_SLIPPAGE_BPS_PER_SIDE))`
   - 注意：h3 现有 config.json 显式传 1.0，因此本次改动**不改变任何现有调用行为**，只改无 config 时的 fallback
3. `write_winner_trades.py` L69-70 用的是 `cfg["fees_bps_per_side"]`（无 fallback，KeyError 即暴露），**不改**

**验收**：
```bash
cd /Users/mark/multica/quant-loop
ls strategies/mtf_xs_pairs_1m_15m_2h_h{1,2,3,4}_20260718/results/cost_migration_note.md | wc -l   # == 4
grep -c '44bps\|0\.0044' strategies/mtf_xs_pairs_1m_15m_2h_h3_20260718/results/cost_migration_note.md   # >= 1
$PY -m py_compile strategies/mtf_xs_pairs_1m_15m_2h_h3_20260718/sizing_sweep.py && echo COMPILE_OK
grep -n 'get("fees_bps_per_side", 1.0)\|get("slippage_bps_per_side", 1.0)' strategies/mtf_xs_pairs_1m_15m_2h_h{1,2,3,4}_20260718/*.py | wc -l   # == 0
git diff --stat -- 'strategies/mtf_xs_pairs_*/config*.json' | wc -l   # == 0（config 零改动，只读验证）
```

---

## T14B — base 模块成本常量迁移（**冻结任务：W4 完成后才放行**）

- **目标**：`mtf_xs_pairs_base_20260718.py` 的 fee/slip 默认值与 cfg fallback 从 1.0 换成 cost_model 批准常量（4.0/7.0），import 走 `_shared.execution.cost_model` 单一来源；不重跑回测、不改 config.json、不改任何结果文件。
- **机器**：mac ｜ **估时**：20 min ｜ **依赖**：**W4 全部完成（冻结解除）** + T14A（笔记已落）+ W3-T7（同文件 /home/smark 路径迁移已做——若 T7 未做，本任务只动成本行，不代做路径迁移，避免超范围）
- **放行条件（执行前必查）**：`ls quant-loop/research/swarm/2026-07-25/signal-enhance-h3/full_history/results/se_h3_metrics.json` 存在（W4-T15 聚合产物），或收到调度器明确的 W4-done 信号；不满足则**原样退回，不要执行**

**读**：
- `quant-loop/strategies/_indicators/mtf_xs_pairs_base_20260718.py` — L464（`_backtest_pair(..., fee_bps: float = 1.0, slip_bps: float = 1.0)`）、L576（`cost = 2.0 * 2.0 * (fee_bps + slip_bps) / 10_000.0`）、L812-813（`cfg.get("fees_bps_per_side", 1.0)` / `cfg.get("slippage_bps_per_side", 1.0)`）、L847（`run_backtest` 把 fee_bps/slip_bps 传给 `_backtest_pair`）
- `quant-loop/strategies/_indicators/tests/test_mtf_xs_pairs_base_20260718.py` L102-103 — 测试显式传 1.0/1.0，**不受影响，不改**
- `quant-loop/_shared/execution/cost_model.py` L36-52（import 模板）、L61-63（常量名）

**写**（仅 `quant-loop/strategies/_indicators/mtf_xs_pairs_base_20260718.py` 一个文件）：
1. 文件头部按 cost_model.py L36-52 双模式加 import（注意该文件被策略目录以 `from _indicators.mtf_xs_pairs_base_20260718 import ...` 裸模块方式 import，见 h1 strategy.py L12，所以必须有 except 分支把 quant-loop 根加进 sys.path——`Path(__file__).resolve().parents[2]`）：
   ```python
   try:
       from _shared.execution.cost_model import (
           SMA34900_FEE_BPS_PER_SIDE as _FEE_BPS,
           SMA34900_PURE_SLIPPAGE_BPS_PER_SIDE as _SLIP_BPS)
   except ImportError:
       import sys as _sys
       from pathlib import Path as _Path
       _root = str(_Path(__file__).resolve().parents[2])
       if _root not in _sys.path:
           _sys.path.insert(0, _root)
       from _shared.execution.cost_model import (
           SMA34900_FEE_BPS_PER_SIDE as _FEE_BPS,
           SMA34900_PURE_SLIPPAGE_BPS_PER_SIDE as _SLIP_BPS)
   ```
2. L464 改 `fee_bps: float = _FEE_BPS, slip_bps: float = _SLIP_BPS`
3. L812 改 `fee_bps = float(cfg.get("fees_bps_per_side", _FEE_BPS))`；L813 改 `slip_bps = float(cfg.get("slippage_bps_per_side", _SLIP_BPS))`
4. L576 公式本身**不动**（2×2×(fee+slip)/1e4 的结构就是 pair-RT 口径，与 apply_cost(BINANCE_FUTURES) 两腿等价：单腿 RT 22bps × 2 腿 = 44bps/笔）；在 L576 上方加一行注释 `# cost basis: SMA-34900 ratified per-side-per-leg (see _shared/execution/cost_model.py BINANCE_FUTURES); pair RT = 2 legs x 2 sides x (fee+slip)`
5. 在 L812 附近或模块 docstring 注明：「h1-h4 既有 config.json 显式 pin 1.0/1.0（旧 8bps 口径），本改动只影响未显式配置的调用；切换 config 到批准值属主线判决」

**验收**：
```bash
cd /Users/mark/multica/quant-loop
$PY -m py_compile strategies/_indicators/mtf_xs_pairs_base_20260718.py && echo COMPILE_OK
$PY -m pytest strategies/_indicators/tests/test_mtf_xs_pairs_base_20260718.py -q   # 全过（测试显式传 1.0，必须仍绿）
$PY -c "
import sys; sys.path.insert(0, 'strategies')
from _indicators.mtf_xs_pairs_base_20260718 import _backtest_pair
import inspect
sig = inspect.signature(_backtest_pair)
assert sig.parameters['fee_bps'].default == 4.0, sig.parameters['fee_bps'].default
assert sig.parameters['slip_bps'].default == 7.0, sig.parameters['slip_bps'].default
print('DEFAULTS_OK')"
grep -rn 'fees_bps_per_side", 1\.0\|slippage_bps_per_side", 1\.0\|fee_bps: float = 1\.0\|slip_bps: float = 1\.0' strategies/_indicators/mtf_xs_pairs_base_20260718.py | wc -l   # == 0
```

---

## T15A — klines + funding 增量抓取与合并（**冻结任务：W4 完成后才放行**）

- **目标**：把 `data/perp_1m/`（3 币）、`data/perp_30m/`（7 币）、`data/funding/`（7 币）从各自的最后时间戳增量顶到「今天」，只增量不全量。
- **机器**：mac（数据在本机，11G；需能访问 fapi.binance.com）｜ **估时**：25 min ｜ **依赖**：**W4 完成（data parquet 冻结解除）**；软依赖 W3-T4/T5（fetch 脚本 `/home/smark` 迁移）——**可用显式 `--out-dir` 绕过，不阻塞**
- **放行条件**：同 T14B（W4 聚合产物存在）；另执行前先 `date -u` 取真实当前日期，不要用会话里的旧日期

**关键事实（已核实，决定实现方式）**：
- 三个 fetch 脚本都是「抓指定窗口 → 整文件覆盖写」，**自身不带与既有 parquet 的合并逻辑**：`scripts/fetch_binance_usdm_1m.py` L261 `df.to_parquet(parquet_path)`（只写本次抓的 df）；`scripts/fetch_binance_funding.py` L80 同；`scripts/fetch_binance_usdm_30m.py` L141 同。所以增量 = 抓到 staging 目录 + 自建 merge 步骤
- `scripts/fetch_binance_usdm_1m.py`：CLI L194-199 `--symbols --start --end --out-dir --format`；`DEFAULT_OUT_DIR` 是 `/home/smark/...`（L44）→ 必须显式传 `--out-dir`；schema 12 列含 `open_time`（ms）；`--format parquet`（默认 `parquet,csv`，csv 会慢，显式只 parquet）
- `scripts/fetch_binance_usdm_30m.py`：CLI L111-114 同构，默认 7 币
- `scripts/fetch_binance_funding.py`：CLI L63-66；**坑1**：`--end` 默认值是写死的 `"2026-07-11"`（L65），必须显式传今天；**坑2**：默认 symbols 只有 `BNBUSDT,DOGE,AVAX,LINK`（L63），BTC/ETH/SOL 要显式传全 7 币；**坑3**：funding schema 是 `ts,symbol,fundingRate,markPrice`（时间列叫 `ts`，不是 `open_time`）；输出 `data/funding/{SYMBOL}.parquet`
- 现有数据最后时间：klines ≈ 2026-07-17、funding ≈ 2026-07-17（以实际读到的为准，脚本里动态取）

**写**：
1. 新建 `quant-loop/scripts/merge_incremental_parquet.py`（唯一新代码文件）：
   - CLI：`--existing <path> --incoming <path> --key <col> --out <path>`
   - 逻辑：读两个 parquet → `pd.concat` → `drop_duplicates(subset=[key], keep="last")` → 按 key 排序 → 断言 key 严格递增无重复 → 写到 `--out`（先写 `out + ".tmp"` 再 `os.replace`，防中途损坏）；打印 old_rows / new_rows / merged_rows / first_ts / last_ts
   - funding 调用时 `--key ts`，klines 用 `--key open_time`
2. 新建 `quant-loop/scripts/incremental_refresh_data.sh`（编排脚本，bash）：
   ```bash
   #!/bin/bash
   set -euo pipefail
   PY=/Users/mark/sdk/mamba-envs/trading/bin/python3
   QL="$(cd "$(dirname "$0")/.." && pwd)"
   TODAY="$(date -u +%F)"
   STAGE="$(mktemp -d /tmp/ql_refresh.XXXXXX)"
   BAK="$(mktemp -d /tmp/ql_backup.XXXXXX)"
   # 1) 备份
   cp "$QL"/data/perp_1m/*.parquet "$QL"/data/perp_30m/*.parquet "$QL"/data/funding/*.parquet "$BAK"/
   # 2) 取每类最后日期（用 merge 脚本同风格的一行 python；open_time/ts 都是 ms epoch）
   LAST_1M=$($PY -c "import pandas as pd,glob;print(max(pd.read_parquet(f,columns=['open_time'])['open_time'].max() for f in glob.glob('$QL/data/perp_1m/*_1m.parquet'))//1000)" )
   LAST_1M_ISO=$($PY -c "from datetime import datetime,timezone;print(datetime.fromtimestamp($LAST_1M,tz=timezone.utc).strftime('%Y-%m-%d'))")
   # （30m / funding 同法；funding 读 columns=['ts']）
   # 3) 抓取到 staging
   $PY "$QL/scripts/fetch_binance_usdm_1m.py" --symbols BTCUSDT,ETHUSDT,SOLUSDT --start "$LAST_1M_ISO" --end "$TODAY" --out-dir "$STAGE/perp_1m" --format parquet
   $PY "$QL/scripts/fetch_binance_usdm_30m.py" --symbols BTCUSDT,ETHUSDT,SOLUSDT,BNBUSDT,DOGEUSDT,AVAXUSDT,LINKUSDT --start "$LAST_30M_ISO" --end "$TODAY" --out-dir "$STAGE/perp_30m"
   $PY "$QL/scripts/fetch_binance_funding.py" --symbols BTCUSDT,ETHUSDT,SOLUSDT,BNBUSDT,DOGEUSDT,AVAXUSDT,LINKUSDT --start "$LAST_FUND_ISO" --end "$TODAY" --out-dir "$STAGE/funding"
   # 4) 逐文件合并（incoming 缺失 = 该币抓取失败 → 跳过并告警，不用旧 staging 覆盖）
   for f in "$QL"/data/perp_1m/*_1m.parquet; do
     base="$(basename "$f")"
     [ -f "$STAGE/perp_1m/$base" ] && $PY "$QL/scripts/merge_incremental_parquet.py" --existing "$f" --incoming "$STAGE/perp_1m/$base" --key open_time --out "$f" || echo "WARN: no incoming for $base"
   done
   # （perp_30m 同法 --key open_time；funding 同法 --key ts，文件名 {SYM}.parquet）
   echo "BACKUP_DIR=$BAK"   # 留给验收/回滚
   ```
   把省略号处补全后 `chmod +x`。**注意 fetch_30m 是否也有 `--format` 参数要现场确认（grep add_argument），有则显式 parquet**。
3. 执行 `bash scripts/incremental_refresh_data.sh`（网络抓取 ~8 天增量：1m 每币 ~12 页、秒级；30m/funding 更快；总耗时预估 <10 min）

**验收**：
```bash
cd /Users/mark/multica/quant-loop && $PY -c "
import pandas as pd, glob, time
now = time.time()
for f in sorted(glob.glob('data/perp_1m/*_1m.parquet') + glob.glob('data/perp_30m/*_30m.parquet')):
    df = pd.read_parquet(f, columns=['open_time'])
    assert df['open_time'].is_unique and df['open_time'].is_monotonic_increasing, f
    age_h = (now - df['open_time'].max()/1000)/3600
    assert age_h < 24, (f, age_h)
    print(f, 'last_age_h=%.1f' % age_h)
for f in sorted(glob.glob('data/funding/*.parquet')):
    if f.endswith('.csv'): continue
    df = pd.read_parquet(f, columns=['ts'])
    age_h = (now - df['ts'].max().timestamp())/3600
    assert age_h < 24, (f, age_h)
    print(f, 'last_age_h=%.1f' % age_h)
print('REFRESH_OK')"
```
（若某币 Binance 端本身无更新导致 age 略超，如实记录到 stdout 不硬 fail——但 1m klines 必然有更新。）

---

## T15B — resample 5m/15m + manifest + inventory 更新

- **目标**：1m 刷新后重建 `data/perp_5m/`、`data/perp_15m/` 及 manifest，并刷新数据盘点。
- **机器**：mac ｜ **估时**：20 min ｜ **依赖**：T15A（1m 已顶到今天）+ W3-T3（`scripts/build_data_inventory.py` 已存在——**执行前先 `ls quant-loop/scripts/build_data_inventory.py` 确认，不存在则退回并注明阻塞**）

**读**：
- `quant-loop/scripts/build_perp_resampled_manifest.py` — L57 `QUANT_LOOP_ROOT = Path(__file__).resolve().parents[1]`（无 /home/smark 问题）；L414-419 CLI：`--date`（默认今天）、`--dry-run`、`--summary-json`。它做全量 1m→5m/15m 重采样 + 写 `data/manifests/perp_resampled_<YYYY-MM-DD>.yaml`（含 sha256/行数/连续性校验，docstring L1-40）
- `quant-loop/data/manifests/perp_resampled_2026-07-24.yaml`（上一版格式参考）

**写 / 执行**：
1. `$PY scripts/build_perp_resampled_manifest.py --dry-run` 先看摘要无异常（预估全量 resample 3 币 × ~2.4M 行，分钟级；`time` 记录耗时）
2. `$PY scripts/build_perp_resampled_manifest.py` 正式跑 → 产出 `data/perp_5m/*.parquet`、`data/perp_15m/*.parquet`、`data/manifests/perp_resampled_<今天>.yaml`
3. `$PY scripts/build_data_inventory.py` 重跑 → 更新 `data/README.md` + `data/manifests/inventory.yaml`（T3 产物，幂等生成器）
4. **不删**旧 manifest `perp_resampled_2026-07-24.yaml`（历史证据）

**验收**：
```bash
cd /Users/mark/multica/quant-loop
ls data/manifests/perp_resampled_$(date -u +%F).yaml   # 存在
$PY -c "
import yaml, time
m = yaml.safe_load(open('data/manifests/perp_resampled_$(date -u +%F).yaml'))
print('manifest loaded OK')"
$PY scripts/build_data_inventory.py --check && echo INVENTORY_IDEMPOTENT   # 重跑无 diff
$PY -c "
import pandas as pd, time
df = pd.read_parquet('data/perp_15m/BTCUSDT_15m.parquet', columns=['open_time'])
age_h = (time.time() - df['open_time'].max()/1000)/3600
assert age_h < 24, age_h
assert df['open_time'].is_unique
print('RESAMPLE_OK last_age_h=%.1f' % age_h)"
```

---

## 依赖与冲突汇总（给 parent / 调度器）

```
T13  ───────────── 随时（批次 1，零冻结冲突）
T14A ───────────── 随时（批次 1；软依赖 W3-T12）
T14B ── W4 完成 + T14A + W3-T7 ──►（批次 2，先于此批其他）
T15A ── W4 完成（data parquet 冻结解除）──► T15B ── 另需 W3-T3
```

跨 slice 冲突：
1. **W4（w4-signal-enhance-h3）**：T14B 与 T15A/B 均须等 W4 冻结解除（解除点 = W4 聚合任务产出 `full_history/results/se_h3_metrics.json`）。T15A 追加 data parquet 会直接打破 W4 的 `len==2448219` 与窗口边界断言——**绝不可与 W4 并发**。
2. **adapter 收敛工作流**：本 slice 不碰 `framework_adapter_*.py`；h3 `framework_validate.py` 的 FREQTRADE/BACKTRADER fee 常量也留给他们，已在 T14A 笔记中标注。
3. **compare/ledger 工作流**：T13 改默认值后，任何不显式传 `cost_bps_rt` 的重跑口径从 24→22bps；需知会对方在重跑脚本中显式 pin 值。
4. **W3 内部**：T14B 与 W3-T7 同改 `mtf_xs_pairs_base_20260718.py`（T7 改路径、T14B 改成本默认值）——必须串行，T7 先行。
