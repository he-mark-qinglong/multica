# w5-s3 — W5/T8-T10 细化：paper 原子 ledger writer + runner 骨架 + graveyard 修复工具（round-2 执行卡）

- slice: `w5-s3`（父 workstream: `w5-automation-ops`，G3 paper trading 组）
- 日期: 2026-07-25
- 本 slice 含 3 张卡：**T8**（原子 ledger writer）→ **T9**（runner 骨架断点续跑）、**T10**（graveyard ledger 修复）。T9/T10 都依赖 T8 的 `ledger_writer.py`，二者之间文件不相交可并行。
- 全局约束（三张卡共用，执行 agent 必读）:
  - python 一律 `/Users/mark/sdk/mamba-envs/trading/bin/python3`（系统 python3 没有 pyarrow/pandas 保证）。
  - **只准新建** `quant-loop/_shared/paper/` 下的文件（目录当前**不存在**，由 T8 创建）+ T10 允许在 graveyard 的 `results-ledger/` 内新建**一个**输出文件 `daily_metrics.repaired.csv`。
  - **禁止修改** 旧 harness 的任何一个字：`quant-loop/strategies/_graveyard/paper_trading/paper_trading_mtf_xs_pairs_eth_sol_20260719/` 下的 `paper_runner.py` / `fill_engine.py` / `kill_criteria.py` / `config.json` / `ARCHIVE_NOTE.md` / `RUNBOOK.md` / `results-ledger/daily_metrics.csv` / `equity_curve.csv` / `trades.jsonl` / `system.jsonl` 全部只读（plan §11：不给旧 harness 续命，只重写）。
  - **禁止修改** `_shared/run_backtest.py` 及 `_shared/` 下任何既有文件（验证管线归别的 workstream）。
  - 禁止任何 git 操作；worktree 有别人的未提交改动，不许碰。
  - 不跑任何真实回测、不访问网络、不碰 live 数据目录。测试只用合成数据，单卡 pytest <2min。
  - 新代码里**禁止硬编码绝对路径**（旧 `paper_runner.py:25` 硬编码 `/home/smark/multica/quant-loop` 是反面教材）；一律用 `Path(__file__).resolve()` 相对定位或 CLI 参数传入。

---

## 背景（已逐行核实的代码事实，三张卡共用）

**旧 bug 实证**（只读引用，作为新 writer 的反例规格）:

- `quant-loop/strategies/_graveyard/paper_trading/paper_trading_mtf_xs_pairs_eth_sol_20260719/paper_runner.py:103-110`
  `_append_daily_metrics`：纯 `open("a")` append，无按日去重、无尾部换行不变量；
  `:113-124` `_init_ledger_headers` 单独 `write_text(header)` 且 header 末尾虽有 `\n`，但 append 路径在
  文件非空时不写 header —— 两条路径竞争导致 **header 与首行数据粘连**。
- `ARCHIVE_NOTE.md:25-33` 记录实证：`daily_metrics.csv` 有两行 `2026-07-20`
  （net_pnl -892.940945 vs -891.756663 不一致），且物理上 header 行尾巴直接粘着第一行数据
  （`...kill_reason,notes2026-07-20,13,...`）。
- 实测（2026-07-25，trading python）graveyard `results-ledger/`:
  - `daily_metrics.csv` 只有 **2 个物理行**（粘连行 + 1 正常行），header 字段表（`paper_runner.py:117-124`）:
    `date,total_trades,winning_trades,losing_trades,win_rate,gross_pnl_usd,net_pnl_usd,fees_usd,slippage_usd,equity_usd,daily_return_pct,rolling_20d_sharpe,rolling_20d_pf,max_drawdown_pct,max_drawdown_pct_vs_backtest,profit_factor_lifetime,bootstrap_ci_lo,action,kill_triggered,kill_reason,notes`
  - `trades.jsonl` **26 行全部 `"kind": "fill"`**，`ts` 全部落在 `2026-07-20`（UTC）。
    每行 schema（首行实测）: 顶层 `ts, ts_exchange, kind, client_order_id, order_id, strategy_id, symbol, side, qty, price, notional_usd, commission, commission_asset, liquidity, trade_id, balance_after, position_after_qty, position_after_avg_price, realized_pnl_after, tags`；
    `tags` 内含 `tf, edge, pair, direction, entry_ts, entry_price_a/b, exit_price_a/b, z_at_entry, z_at_exit, funding_ema_at_entry, exit_reason, bars_held, pnl_pct_gross, pnl_pct_net, fees_usd, slippage_usd`。
  - **按 `tags.entry_ts` 分组 = 13 个不同交易**（= ledger `total_trades=13` ✓）；每组 2 个 fill（A/B 两腿），
    两腿的 `tags` 完全重复 ⇒ `tags.fees_usd` 全表求和 12.843941 = **2×** ledger 的 6.42551，
    `tags.slippage_usd` 同理 8.562630 = 2× 4.283673。⇒ 重建时**每个 entry_ts 只取首条 fill 的 tags**。
  - 每笔交易两腿 `realized_pnl_after` 之和 > 0 的恰好 **4 笔**（+29.98/+321.04/+348.42/+1197.32），
    亏 9 笔 ⇒ win_rate 4/13 = 0.307692，与 ledger `winning_trades=4, losing_trades=9` **精确一致** ✓。
  - 最后一条 fill 的 `balance_after = 99108.243337` ⇒ `99108.243337 - 100000 = -891.756663`，
    与 ledger 第 2 行 `net_pnl_usd=-891.756663` **逐位一致** ✓（第 1 行 -892.94 是旧 writer 另一次
    中途计算的 stale 值——双行差异本身就是要量化的 bug 证据）。
  - `equity_curve.csv` 26 行 + 粘连 header（同样缺换行）；`system.jsonl` 52 行，最后事件
    `session_end @ 2026-07-20T14:11:48Z`。

**旧 config 结构**（T9 schema 的参照，`config.json` 实测关键字段）:
`issue_id/issue_identifier/strategy_target/instruments/pair/timeframe/venue`（:2-19）、
`starting_capital_usd=100000`（:38）、`backtest_expectations.{oos_sharpe,profit_factor,backtest_max_dd_pct?,...}`（:42-50）、
`kill_criteria.{min_trades_before_kill_check=100, min_live_profit_factor=1.0, max_drawdown_multiple_vs_backtest=1.5, rolling_20d_sharpe_floor=0.0, smark_absolute_max_dd_pct=5.0, smark_absolute_daily_loss_pct=2.0}`（:52-62）。

**kill 评估参考实现**（`paper_runner.py:63-100` `_evaluate_kill_criteria`，可移植不可 import——旧文件在
graveyard 包里、且 T9 要自包含）：三条硬规则 ① `n_trades >= min_trades_before_kill_check` 且
`profit_factor_lifetime < min_live_profit_factor` ② `|max_dd_pct| > max_drawdown_multiple_vs_backtest × |backtest_max_dd_pct|`
③ `rolling_20d_sharpe < rolling_20d_sharpe_floor`；已触发则保持触发（latch）。

**`_shared/run_backtest.py` 信号接口**（T9 消费，只读）:
- `Trade` dataclass（:78-87）: `entry_ts: pd.Timestamp, exit_ts: pd.Timestamp, direction: Literal["long","short"], size_fraction: float = 1.0`。
- `run_backtest(bars, trades, *, initial_capital=100_000.0, cost_bps_rt=24.0, cost_mode="fill", freq_per_year=365*24)`（:171-179）
  → `{"equity": pd.Series(index=bars.index), "metrics": {sharpe, annualised_pct, total_return_pct, max_drawdown_pct, n_bars}, "n_trades", "n_skipped"}`。
  `bars` 必须 UTC timestamp 索引 + `close` 列。纯函数无 I/O（模块 docstring :61-62）。

**pytest 约定**（全仓库无 conftest.py / pytest.ini，pytest 从 repo 根调用）:
测试文件自行 `sys.path.insert`。`_shared/test_run_backtest.py:44` 的写法是
`sys.path.insert(0, str(Path(__file__).resolve().parents[1]))`；本 slice 测试文件在
`quant-loop/_shared/paper/` 下，要用 **`parents[2]`**（= quant-loop 根）然后
`from _shared.paper.ledger_writer import ...`。

**统一验收前缀**: `PY=/Users/mark/sdk/mamba-envs/trading/bin/python3`

---

## T8 — paper 原子 ledger writer（`quant-loop/_shared/paper/`）

- **目标（一句话）**：新建原子、幂等、按 date 去重的 daily-metrics ledger writer + trades.jsonl 重建函数，彻底消灭旧 harness 的双行/粘连两类 bug。
- **机器**: mac | **预估**: 25 min | **依赖**: 无
- **读（只读，取证用）**:
  - `quant-loop/strategies/_graveyard/paper_trading/paper_trading_mtf_xs_pairs_eth_sol_20260719/paper_runner.py:103-124`（反例：bug 的两个来源）
  - `quant-loop/_shared/test_run_backtest.py:40-47`（sys.path 约定样板）
  - `quant-loop/strategies/_graveyard/paper_trading/paper_trading_mtf_xs_pairs_eth_sol_20260719/results-ledger/trades.jsonl`（重建函数的输入 schema，见上方背景）
- **写（全部新建）**:
  - `quant-loop/_shared/paper/__init__.py` — 一行 docstring，参照 `_shared/__init__.py`（全文件就 1 行）。
  - `quant-loop/_shared/paper/ledger_writer.py`
  - `quant-loop/_shared/paper/test_ledger_writer.py`

### T8 逐步实现

1. `__init__.py`：`"""Atomic paper-trading ledger writer (T8, infra-sprint 2026-07-25)."""`
2. `ledger_writer.py` 公开 API（三个函数 + 一个常量）：

   ```python
   DAILY_FIELDS = ["date","total_trades","winning_trades","losing_trades","win_rate",
       "gross_pnl_usd","net_pnl_usd","fees_usd","slippage_usd","equity_usd",
       "daily_return_pct","rolling_20d_sharpe","rolling_20d_pf","max_drawdown_pct",
       "max_drawdown_pct_vs_backtest","profit_factor_lifetime","bootstrap_ci_lo",
       "action","kill_triggered","kill_reason","notes"]   # 与 paper_runner.py:117-124 逐字一致

   def append_daily_row(ledger_dir: Path, row: dict, fieldnames=DAILY_FIELDS) -> Path
   def rebuild_daily_metrics(trades_path: Path, starting_capital: float) -> list[dict]
   def write_daily_csv(path: Path, rows: list[dict], fieldnames=DAILY_FIELDS) -> None
   ```

3. `append_daily_row` 核心逻辑（**这是本卡的关键 tricky 点**，照此骨架写）：
   - 目标文件 `ledger_dir/"daily_metrics.csv"`。**不走 append 模式**；流程：
     a. 若文件存在且非空：用 `csv.DictReader` 读全部旧行（文件保证是自己写的干净格式；
        读到粘连/坏行直接 `raise ValueError`，不静默吞）。
     b. `rows = [r for r in old if r["date"] != row["date"]]` + 追加新行 → **同 date 幂等覆盖**。
     c. 按 `date` 排序。
     d. 写临时文件：`tmp = path.with_suffix(".csv.tmp")`（同目录，保证同文件系统），
        `csv.DictWriter` 写 header + 全部行，**每行 `\n` 结尾，文件末尾必须恰好一个 `\n`**。
     e. `os.replace(tmp, path)` —— 原子替换，崩溃只会留 `.tmp`，目标文件永不出现半行。
   - 不变量（写进 docstring 并由测试断言）：文件第一行 = `",".join(fieldnames)`；
     文件以 `\n` 结尾；每个 `date` 至多一行。
4. `rebuild_daily_metrics(trades_path, starting_capital)`（T10 的直接依赖）：
   - 逐行 `json.loads`；只收 `kind == "fill"` 的行，其他 kind 跳过并计数。
   - 按 `tags.entry_ts` 分组为交易（一组 = 一笔 pair 交易，含 A/B 两腿 fill）。
   - 每笔交易：`fees = 首条fill.tags.fees_usd`（两腿 tags 重复，**只取一次**，见背景实测 2× 现象）、
     `slippage` 同理；`trade_pnl = sum(两腿 realized_pnl_after)`；`win = trade_pnl > 0`。
   - 按 fill `ts` 的 UTC date 聚合出每日行：
     `total_trades/winning_trades/losing_trades/win_rate`（6 位小数 round）、
     `fees_usd/slippage_usd`（该日所有交易求和）、
     `net_pnl_usd = 当日最后一条 fill 的 balance_after − 前一日最后一笔 balance_after（首日减 starting_capital）`、
     `gross_pnl_usd = net_pnl_usd + fees_usd + slippage_usd`、
     `equity_usd = 当日最后 balance_after`、
     `daily_return_pct = net_pnl_usd / 前日 equity（首日用 starting_capital）× 100`。
     其余 rolling/kill 字段本函数不算：填 `0.0`/`False`/`""`/`action="REBUILT"`、`notes="rebuilt from trades.jsonl"`。
   - 返回按 date 排序的 row dict 列表（不写盘；写盘走 `write_daily_csv`）。
5. `write_daily_csv`：就是 `append_daily_row` 的 c-e 步独立出来（全量覆写，供 T10 输出 repaired 文件）。
6. `test_ledger_writer.py`（头部 `sys.path.insert(0, str(Path(__file__).resolve().parents[2]))`，
   `from _shared.paper.ledger_writer import ...`）至少 5 个用例，全部用 `tmp_path` fixture：
   - `test_header_written_once_with_trailing_newline`：append 一行 → 文件恰好 2 行，首行 = 字段表，
     末字符 `\n`，且 `header,first_row = content.split("\n")[:2]` 可分离（**回归旧粘连 bug**）。
   - `test_same_date_upsert_no_duplicate`：同 date 追加两次不同值 → 文件仍 2 行，值为第二次的
     （**回归旧双行 bug**）。
   - `test_interrupted_write_leaves_no_partial_row`：monkeypatch `os.replace` 抛异常 →
     原文件字节不变，只剩 `.tmp` 残留；再正常 append 一次成功且行数正确。
   - `test_rebuild_roundtrip`：造 3 笔合成 fill（2 笔 2026-01-01、1 笔 2026-01-02，tags 两腿重复）
     → `rebuild_daily_metrics` 返回 2 行：day1 `total_trades=2`、`fees_usd` = 单腿和（非双倍）、
     `net_pnl_usd` = balance 差；`write_daily_csv` 落盘后重新 `append_daily_row` 一行 2026-01-03
     → 3 行、header 仍只有 1 行。
   - `test_rebuild_skips_non_fill`：混一行 `"kind": "system"` → 被跳过且结果行数不变。

### T8 验收

```bash
PY=/Users/mark/sdk/mamba-envs/trading/bin/python3
cd /Users/mark/multica && $PY -m pytest quant-loop/_shared/paper/test_ledger_writer.py -q
# 期望：5 passed（或更多），0 failed，<10s
grep -c "def append_daily_row\|def rebuild_daily_metrics\|def write_daily_csv" quant-loop/_shared/paper/ledger_writer.py
# 期望：3
```

---

## T9 — paper runner 骨架（config 驱动 + 断点续跑 + kill 评估）

- **目标（一句话）**：基于 T8 writer 的最小离线 paper runner：config.json 驱动、消费 `_shared.run_backtest` 接口、state.json 幂等断点续跑、kill 触发即停。
- **机器**: mac | **预估**: 25 min | **依赖**: T8（import `_shared.paper.ledger_writer`）
- **读（只读）**:
  - `quant-loop/_shared/run_backtest.py:78-87`（`Trade` 字段）、`:171-218`（`run_backtest` 签名与返回键）——**只 import，不修改**。
  - `quant-loop/strategies/_graveyard/paper_trading/paper_trading_mtf_xs_pairs_eth_sol_20260719/paper_runner.py:63-100`（kill 规则参考逻辑，移植不 import）
  - 同目录 `config.json:38-62`（config 字段参照：`starting_capital_usd`、`backtest_expectations`、`kill_criteria` 六个阈值键）
  - `quant-loop/_shared/paper/ledger_writer.py`（T8 产物：`append_daily_row`、`DAILY_FIELDS`）
- **写（全部新建）**:
  - `quant-loop/_shared/paper/runner.py`
  - `quant-loop/_shared/paper/config.schema.json`
  - `quant-loop/_shared/paper/test_runner.py`

### T9 逐步实现

1. 定位纪律：`runner.py` 顶部 `QUANT_LOOP_ROOT = Path(__file__).resolve().parents[2]`，
   `sys.path.insert(0, str(QUANT_LOOP_ROOT))`，再 `from _shared.run_backtest import Trade, run_backtest`
   和 `from _shared.paper.ledger_writer import append_daily_row, DAILY_FIELDS`。**禁止写死绝对路径。**
2. `config.schema.json`（JSON Schema draft-07，仅文档+可选校验用；不要求引入 jsonschema 依赖，
   runner 内手写必填键检查即可）。必填键：
   `strategy_id`(str)、`timeframe`(str)、`starting_capital_usd`(number>0)、
   `cost_bps_rt`(number≥0)、`freq_per_year`(int>0)、
   `backtest_expectations.backtest_max_dd_pct`(number)、
   `kill_criteria.{min_trades_before_kill_check, min_live_profit_factor, max_drawdown_multiple_vs_backtest, rolling_20d_sharpe_floor}`。
   在 schema 里把这 4 个 kill 键全标 `required`。
3. `runner.py` 结构（单文件，约 150 行）：

   ```python
   def load_config(path: Path) -> dict          # json.load + 必填键检查，缺键 raise KeyError(键名)
   def load_state(run_dir: Path) -> dict        # state.json 不存在 → {"last_date": null, "killed": false, "kill_reason": ""}
   def save_state(run_dir: Path, state: dict)   # tmp + os.replace（与 T8 同原子纪律）
   def evaluate_kill(day_row: dict, cfg: dict, state: dict) -> dict
       # 移植 paper_runner.py:63-100 三规则 + latch（state["killed"] 为真直接返回）
   def run(cfg_path: Path, bars_csv: Path, trades_csv: Path, run_dir: Path) -> int
   ```

4. `run()` 主流程（离线批模式，替代旧 live loop；断点续跑 = 按日跳过）：
   a. `bars_csv` → `pd.read_csv(parse_dates=["ts"], index_col="ts")`，`trades_csv` 含
      `entry_ts,exit_ts,direction,size_fraction` 四列 → `[Trade(...)]`。
   b. 一次性调 `run_backtest(bars, trades, initial_capital=cfg["starting_capital_usd"],
      cost_bps_rt=cfg["cost_bps_rt"], freq_per_year=cfg["freq_per_year"])` 拿 `equity` Series。
   c. 按 UTC date 分组 equity，逐日推进：`if state["killed"]: break`；
      `if state["last_date"] and date <= state["last_date"]: continue`（**幂等关键点：重跑不重复记账**）。
   d. 每日组 `day_row`：`equity_usd`=当日末 equity、`daily_return_pct`、`net_pnl_usd`=当日 equity 差分、
      `max_drawdown_pct`（用 `equity.cummax` 截至当日的累计 DD，与 `run_backtest.py:118-120` 同式）、
      `n_trades` 用 exit_ts 落在当日的交易数；winning/losing 用当日平仓交易的净 pnl 符号；
      凑齐 `DAILY_FIELDS` 其余键（rolling_* 填 0.0，action="RUN"）。
   e. `evaluate_kill` → 触发则 `day_row["action"]="HALT"`、`kill_triggered=True`、写 reason。
   f. `append_daily_row(run_dir/"results-ledger", day_row)`（T8 函数，自动按日去重）→
      `save_state(run_dir, {"last_date": date, "killed": ..., "kill_reason": ...})`。
   g. 返回码：kill 触发 → 2；正常跑完 → 0（对齐旧 `cmd_kill_check` 的退出码语义，`paper_runner.py:200-204`）。
5. `test_runner.py`（sys.path 同 T8，`parents[2]`）用 `tmp_path` + 合成数据，**不调真实 parquet、不联网**：
   - 合成 bars：30 根 30m bar（两天：day1 20 根、day2 10 根），close 用固定 seed 的随机游走；
     合成 trades：4 笔（2 赢 2 亏，exit 跨两天）。
   - `test_first_run_writes_ledger_and_state`：跑 `run()` → 退出 0；`results-ledger/daily_metrics.csv`
     恰好 1 header + 2 数据行；`state.json` 的 `last_date` = day2、`killed=false`。
   - `test_resume_is_idempotent`：同参数再跑一次 → 退出 0，csv **仍 2 数据行**（断点续跑不重复，
     本卡核心验收），state 不变。
   - `test_kill_latches`：config 把 `rolling_20d_sharpe_floor` 设为 `999.0`（必然触发规则③）
     → 退出码 2；csv 至少一行 `kill_triggered=True` 且 `action=HALT`；再跑一次退出仍 2 且 csv 行数不增。
   - `test_missing_config_key_raises`：删掉 `kill_criteria.min_live_profit_factor` →
     `load_config` 抛 `KeyError` 且消息含键名。

### T9 验收

```bash
PY=/Users/mark/sdk/mamba-envs/trading/bin/python3
cd /Users/mark/multica && $PY -m pytest quant-loop/_shared/paper/test_runner.py -q
# 期望：4 passed，0 failed，<2min（纯合成数据，无真实回测）
$PY -c "import json; json.load(open('quant-loop/_shared/paper/config.schema.json'))" && echo SCHEMA-OK
# 期望：SCHEMA-OK
```

---

## T10 — graveyard ledger 修复工具（trades.jsonl → repaired CSV）

- **目标（一句话）**：用 T8 的 `rebuild_daily_metrics` 从 graveyard 的 `trades.jsonl` 重建 `daily_metrics.repaired.csv`（新文件，原件不动），并打印与原双行的量化差异。
- **机器**: mac（graveyard 数据在本机仓库内）| **预估**: 15 min | **依赖**: T8
- **读（只读）**:
  - `quant-loop/_shared/paper/ledger_writer.py`（T8 产物：`rebuild_daily_metrics`、`write_daily_csv`）
  - `quant-loop/strategies/_graveyard/paper_trading/paper_trading_mtf_xs_pairs_eth_sol_20260719/results-ledger/trades.jsonl`（26 行 fill，schema 见本文件背景节）
  - 同目录 `daily_metrics.csv`（2 物理行，bug 实证原件——**只读对比用，绝不写**）
- **写**:
  - `quant-loop/_shared/paper/repair_ledger.py`（新建）
  - `quant-loop/strategies/_graveyard/paper_trading/paper_trading_mtf_xs_pairs_eth_sol_20260719/results-ledger/daily_metrics.repaired.csv`（**唯一允许落进 graveyard 的新文件**，由脚本运行时生成）

### T10 逐步实现

1. `repair_ledger.py` 头部：`sys.path.insert(0, str(Path(__file__).resolve().parents[2]))`，
   `from _shared.paper.ledger_writer import rebuild_daily_metrics, write_daily_csv`。
2. CLI：`repair_ledger.py <results-ledger-dir> [--capital 100000.0]`（argparse，位置参数 = 含
   `trades.jsonl` 的目录）。拒绝路径里不存在 `trades.jsonl` 的目录（`SystemExit` + 明确报错）。
3. 流程：
   a. `rows = rebuild_daily_metrics(dir/"trades.jsonl", starting_capital=args.capital)`。
   b. `write_daily_csv(dir/"daily_metrics.repaired.csv", rows)`（T8 原子写；**只写这个新文件名，
      永不碰 `daily_metrics.csv`**——在代码里加一句硬断言 `assert out.name == "daily_metrics.repaired.csv"`）。
   c. 对比报告（stdout）：若 `daily_metrics.csv` 存在，容忍粘连地解析（把 `notes2026-` 这种粘连点
      当分隔提示，用 `pd.read_csv` 失败后回退手工按 `date,` 前缀切行——允许简单粗暴，因为本仓库
      只有这一个实证文件），打印：原件每个 date 的行数、原件 vs repaired 在
      `total_trades/winning_trades/net_pnl_usd/equity_usd` 四列上的逐值 diff。
   d. 退出 0。
4. 已知预期输出（执行 agent 用来 sanity-check，源自背景节实测）：repaired 应恰好 **1 行**
   （date=2026-07-20），`total_trades=13, winning_trades=4, losing_trades=9`，
   `net_pnl_usd=-891.756663`（= 末笔 `balance_after` 99108.243337 − 100000，与原件第 2 行逐位一致），
   `fees_usd=6.425510` 量级（±1e-4，单腿去重后）、`equity_usd=99108.243337`。
   diff 报告应显示原件 date=2026-07-20 有 **2 行** 且两行 net_pnl 差 ≈ 1.184。

### T10 验收

```bash
PY=/Users/mark/sdk/mamba-envs/trading/bin/python3
cd /Users/mark/multica
GRAVE=quant-loop/strategies/_graveyard/paper_trading/paper_trading_mtf_xs_pairs_eth_sol_20260719/results-ledger
$PY quant-loop/_shared/paper/repair_ledger.py $GRAVE
# 期望：退出 0，stdout 含 diff 报告（原件 2026-07-20 = 2 行）
tail -n +2 $GRAVE/daily_metrics.repaired.csv | cut -d, -f1 | sort | uniq -d | wc -l
# 期望：0（每个 date 唯一）
grep -c "2026-07-20,13,4,9" $GRAVE/daily_metrics.repaired.csv
# 期望：1
md5 -q $GRAVE/daily_metrics.csv
# 期望：d41d8cd98f00b204e9800998ecf8427e 之外的**不变**值——执行前后各跑一次，两次输出必须相同（原件未被触碰）
git status --porcelain -- $GRAVE
# 期望：只出现 ?? daily_metrics.repaired.csv 一行（无任何 M）
```

---

## 跨 slice / 跨 workstream 冲突备注

1. **`quant-loop/_shared/` 目录结构**：本 slice 新增 `_shared/paper/` 子包；同 wave 的 W5 G4 slice
   （T11-T13）会新增 `_shared/swarm/` —— 子目录不相交，但两 slice 都在 `_shared/` 下建包，
   若 adapter 收敛 workstream（73→1 generic）重组 `_shared` 需知晓这两个新子目录（round1 §4.3 已预警）。
2. **旧 paper_runner 一字不动**：若其他 slice（如验证管线）提出"顺手修旧 harness"，以本 slice 为准——
   plan §11 明确不续命，旧文件保持考古原件状态。
3. T10 落在 graveyard 的输出文件名固定 `daily_metrics.repaired.csv`，不改原件 ⇒ 与任何"graveyard 只读"
   约定兼容（新增文件而非修改）。
