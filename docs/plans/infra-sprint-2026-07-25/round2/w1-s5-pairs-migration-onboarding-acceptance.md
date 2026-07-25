# 片 w1-s5 — T12/T13 细化 + W1 全链验收脚本（T14）

> 执行者：caocao-m3 cheap agent，零上下文，30 分钟预算。
> Python 一律 `/Users/mark/sdk/mamba-envs/trading/bin/python3`（下称 `$PY`）。
> 除特别说明外，所有命令工作目录均为 `/Users/mark/multica/quant-loop`。
> W1 任务总表见 `docs/plans/infra-sprint-2026-07-25/round1/w1-backtest-engine.md`（本文件已内联所需全部信息，执行时无需读它）。

本片 3 张卡：

| id | 标题 | est | machine | deps |
|----|------|-----|---------|------|
| T12 | pairs_cointegration_1d signals.py 迁移样板 | 28 min | mac | T1（可选）, T4, T8 |
| T13 | onboarding-validation.md 新策略接入文档 | 20 min | mac | T8, T10, T12 |
| T14 | W1 全链验收串联脚本 w1_acceptance.sh | 15 min | mac | T1–T13 全部 |

---

## T12 — pairs_cointegration_1d_20260709 接入 generic pipeline（signals.py 迁移样板）

**目标**：给该策略目录写一个 contract-v2 `signals.py`（信号层 only），并小幅修 `data_loader.py`，使 `validation.oos_harness` 自动路由到 generic 管线（native 走 `_shared.run_backtest`），复现 legacy 交易计划。

**Reads**（均已核实存在）：
- `quant-loop/strategies/pairs_cointegration_1d_20260709/strategy.py` — `build_signals(prices_a, prices_b, cfg)` 在 :115-209（输入两个含 `close` 列、UTC DatetimeIndex 的 df，输出含 `zscore` / `entry_long_spread` / `entry_short_spread` / `exit_signal` / `coint_break` 布尔列的 df）。
- `quant-loop/strategies/pairs_cointegration_1d_20260709/config.json` — `timeframe="1d"`，`instruments=[BTCUSDT,ETHUSDT,SOLUSDT]`，`signal.entry_threshold=2.0 / exit_threshold=0.5 / stop_sigma_threshold=4.0`，`position_sizing.leg_pct_per_pair=0.05`，`fees_bps_per_side=2.0`，`slippage_bps_per_side=2.0`。
- `quant-loop/strategies/pairs_cointegration_1d_20260709/data/fapi_{BTC,ETH,SOL}USDT__1d.parquet` — 已缓存的 1d 数据（~732 根/币，2024-06-22..2026-06-23），mac 上可直接用。
- `quant-loop/validation/generic_harness.py` — 路由判定 `is_generic_variant`（:371-373，目录有 `signals.py` 即走 generic）；`run_generic_from_variant`（:376-407）调用 `data_loader.load_all(symbols, timeframe)`（**位置参数**，:402）和 `signals_mod.generate_signals(df, dict(config))`（**每 window × 每 symbol 各调一次，传单个 symbol 的 df**，:274-275, :303）；off-bar 交易被静默跳过（:144-145）。
- `quant-loop/validation/adapters/native_engine.py:39-52` — `_load_module` 在 import 期把 variant 目录插进 `sys.path`，**import 结束后移除**。推论：`signals.py` 顶部可以 `from strategy import build_signals`，但**函数体内不得再 lazy import 本目录模块**。
- `quant-loop/_shared/run_backtest.py:77-87` — `Trade(entry_ts, exit_ts, direction, size_fraction)`，entry/exit 必须在 bars.index 上，`direction ∈ {"long","short"}`。
- legacy 参照：`results/walk_forward.json` 的 `aggregate.oos_sharpe = 3.597`；`results/trades_BTCUSDT_ETHUSDT.csv` 等 4 个逐对交易台账。

**Writes**：
1. `quant-loop/strategies/pairs_cointegration_1d_20260709/signals.py`（新建）
2. `quant-loop/strategies/pairs_cointegration_1d_20260709/data_loader.py`（小改，见步骤 2）

**关键设计约束（必读，踩坑点都在这）**：
- generic harness 调 `generate_signals(df, cfg)` 时**只传单个 symbol 的 df，不传 symbol 名**。signals.py 必须通过"把 df 的 close 序列与本目录 data/ 缓存的三个 1d frame 逐一比对"来识别当前 symbol（窗口切片是全等的子序列，`np.allclose` 可判定）。
- contract 检查器（`_shared/templates/strategy_contract_v2.py:185-218`）则会传 `dict[str, df]`（用 `DEFAULT_CONFIG["symbols"]` 造合成数据）。所以 `generate_signals(bars, config)` 要**同时支持两种形态**：`isinstance(bars, pd.DataFrame)` → harness 单 symbol 模式；`dict` → 合成/字典模式（直接用给定 frame，primary symbol 取 `config.get("primary_symbol")` 或 dict 第一个 key）。首参数名必须是 `bars`（contract :119-122 强制）。
- 只 import `strategy.build_signals`。**不要** import 或调用 `simulate_pair_trades` / `run_backtest` / `portfolio`（portfolio.py 的 lazy import 在 harness 调用期 sys.path 已移除，会炸；且 portfolio 暂停状态机不属于信号层）。
- 本卡是**迁移样板**，不是逻辑复刻：不移植 portfolio 暂停/月度止损状态机，交易计划会与 legacy 有偏差，如实记录即可（见验收第 4 条的 fallback）。

**步骤**：

1. 改 `data_loader.py`：
   - (a) `load_all` 签名改为 `def load_all(symbols=None, timeframe="1d", *, source_root=DEFAULT_SOURCE_ROOT, data_dir=DATA_DIR, refresh=False)`（现签名在 :140-145，第二位置参数是 `source_root`，harness 会把 `"1d"` 当 source_root 传进来——必须改）。函数体开头加 `if timeframe != "1d": raise ValueError(f"only 1d supported, got {timeframe!r}")`。现有调用方全部是 keyword/无参调用（`run_backtest.py:211`、`optimize.py:291`、`tests/test_data_loader.py:191`），不会因 keyword-only 改动而破。
   - (b) `load_symbol_1d`（:120-137）把缓存命中分支挪到 source 检查**之前**：现逻辑先 `if not src.exists(): raise` 再看 cache，导致 mac 上（无 `/home/smark/...` 1m 源）即使缓存存在也报错。改为：
     ```python
     if cache.exists() and not refresh:
         return pd.read_parquet(cache)
     if not src.exists():
         raise FileNotFoundError(f"missing 1m source for {sym}: {src}")
     ```
   - 跑 `$PY -m pytest strategies/pairs_cointegration_1d_20260709/tests/test_data_loader.py -q` 确认仍绿（若原测试断言了"source 缺失即 raise 即使 cache 存在"的旧顺序，把该用例改成"cache 存在时优先返回 cache"并注明原因）。

2. 新建 `signals.py`，骨架（可直接照抄后微调）：
   ```python
   """Contract-v2 signal layer for pairs_cointegration_1d_20260709.

   Wraps strategy.build_signals (rolling hedge + z-score, strategy.py:115)
   into generate_signals(bars, config) -> list[Trade]. Equity walk is owned
   by _shared.run_backtest via the generic harness; this module emits the
   trade schedule only. Pair (A, B) trades are emitted under leg A:
   long_spread -> long A, short_spread -> short A. The short/long B leg is
   NOT modelled (single-symbol engine) — see docs/plans round2 w1-s5 card T12.
   """
   from __future__ import annotations
   from pathlib import Path
   import numpy as np
   import pandas as pd
   from _shared.run_backtest import Trade
   from strategy import build_signals  # sibling import OK at module import time

   DATA_DIR = Path(__file__).resolve().parent / "data"
   PAIRS = [("BTCUSDT", "ETHUSDT"), ("BTCUSDT", "SOLUSDT"), ("ETHUSDT", "SOLUSDT")]

   DEFAULT_CONFIG = {
       "symbols": ["BTCUSDT", "ETHUSDT", "SOLUSDT"],
       "primary_symbol": "BTCUSDT",
       # build_signals 所需键的真实默认值来自 config.json；合成 smoke 跑时
       # 随机游走数据基本不会触发 entry_threshold=2.0，返回空列表也合法。
       "cointegration": {"hedge_window_days": 90, "adf_maxlag": 1},
       "signal": {"zscore_window_days": 30, "entry_threshold": 2.0,
                  "exit_threshold": 0.5, "stop_sigma_threshold": 4.0},
       "position_sizing": {"leg_pct_per_pair": 0.05},
   }

   def _load_cached(symbol: str) -> pd.DataFrame:
       return pd.read_parquet(DATA_DIR / f"fapi_{symbol}__1d.parquet")

   def _identify_symbol(df: pd.DataFrame) -> str | None:
       for sym in ("BTCUSDT", "ETHUSDT", "SOLUSDT"):
           try:
               ref = _load_cached(sym)
           except FileNotFoundError:
               continue
           common = ref.index.intersection(df.index)
           if len(common) == len(df.index) and len(common) > 0 and np.allclose(
               ref.loc[common, "close"].to_numpy(), df["close"].to_numpy()
           ):
               return sym
       return None

   def _pair_trades(df_a, df_b, cfg, size):
       sig = build_signals(df_a, df_b, cfg)
       trades, in_pos, side, entry_ts = [], False, None, None
       for dt, row in sig.iterrows():
           if not np.isfinite(row["zscore"]):
               continue
           if in_pos and (bool(row["coint_break"]) or bool(row["exit_signal"])):
               if dt > entry_ts:
                   trades.append(Trade(entry_ts=entry_ts, exit_ts=dt,
                                       direction=side, size_fraction=size))
               in_pos = False
           elif not in_pos:
               if bool(row["entry_short_spread"]):
                   in_pos, side, entry_ts = True, "short", dt
               elif bool(row["entry_long_spread"]):
                   in_pos, side, entry_ts = True, "long", dt
       return trades

   def generate_signals(bars, config):
       cfg = {**DEFAULT_CONFIG, **(config or {})}
       size = float(cfg["position_sizing"]["leg_pct_per_pair"])
       if isinstance(bars, pd.DataFrame):          # generic-harness mode
           sym = _identify_symbol(bars)
           if sym is None:
               return []
           frames = {s: _load_cached(s).reindex(bars.index) for s in cfg["symbols"]}
           primary = sym
       else:                                        # dict / synthetic mode
           frames = bars
           primary = str(cfg.get("primary_symbol") or next(iter(bars)))
       out = []
       for a, b in PAIRS:
           if a != primary or a not in frames or b not in frames:
               continue
           fa = frames[a].dropna(subset=["close"]); fb = frames[b].dropna(subset=["close"])
           if len(fa) < cfg["cointegration"]["hedge_window_days"] + cfg["signal"]["zscore_window_days"] + 5:
               continue  # window too short for hedge+zscore warmup
           out.extend(_pair_trades(fa, fb, cfg, size))
       return sorted(out, key=lambda t: t.entry_ts)
   ```
   注意：`_load_cached(s).reindex(bars.index)` 会把 partner frame 对齐到当前窗口的 index（harness 已切好片），保证 entry/exit ts 都在 `df.index` 上（否则被引擎静默 skip，generic_harness.py:144）。

3. 冒烟验证（不跑完整回测，秒级）：
   ```bash
   $PY -c "
   import sys; sys.path.insert(0, 'strategies/pairs_cointegration_1d_20260709')
   sys.path.insert(0, '.')
   import signals, data_loader, pandas as pd
   df = pd.read_parquet('strategies/pairs_cointegration_1d_20260709/data/fapi_BTCUSDT__1d.parquet')
   tr = signals.generate_signals(df, {})
   print('BTC trades:', len(tr)); assert len(tr) > 0
   from _shared.templates.strategy_contract_v2 import check_contract
   print(check_contract(signals, n_bars=500))
   "
   ```
   预期：BTC trades > 0（BTC 是 BTC-ETH、BTC-SOL 两对的 leg A）；`check_contract` 返回 `{'ok': True, ...}`。

4. 跑 generic 管线（本卡唯一一次"真跑"，1d × ~732 根 × 3 窗 × 3 币，远低于 2 分钟）：
   ```bash
   $PY -m validation.oos_harness --variant pairs_cointegration_1d_20260709 \
       --frameworks native --windows 3 ; echo "exit=$?"
   ```
   若 T1 已装好 backtrader，再跑 `--frameworks native,backtrader`。verdict 写到 `strategies/pairs_cointegration_1d_20260709/results/validation/verdict.json`。

5. 保真度对比（写进任务结果）：
   ```bash
   $PY -c "
   import json, pandas as pd, sys
   sys.path.insert(0, 'strategies/pairs_cointegration_1d_20260709'); sys.path.insert(0, '.')
   import signals
   df = pd.read_parquet('strategies/pairs_cointegration_1d_20260709/data/fapi_BTCUSDT__1d.parquet')
   new = {str(t.entry_ts.date()) for t in signals.generate_signals(df, {})}
   leg = pd.read_csv('strategies/pairs_cointegration_1d_20260709/results/trades_BTCUSDT_ETHUSDT.csv')
   old = set(pd.to_datetime(leg['entry_date']).dt.strftime('%Y-%m-%d'))
   print('overlap:', len(new & old), '/', len(old))
   "
   ```

**Acceptance（全部机械可跑）**：
1. `$PY -m pytest strategies/pairs_cointegration_1d_20260709/tests/ -q` 全绿。
2. 步骤 4 的 harness 命令 exit code ∈ {0,1}（2 = harness error，算失败）。
3. `$PY -c "import json; r=json.load(open('strategies/pairs_cointegration_1d_20260709/results/validation/verdict.json')); assert r['pipeline']=='generic'; print(r['verdict'])"` 打印 PASS 或 FAIL 均可，但 `pipeline` 必须是 `generic`。
4. 步骤 5 的 entry-date overlap ≥ 60%（BTC-ETH 对）。**Sharpe 偏差 <5% 是 stretch goal**：若未达到但 overlap 达标，在任务结果里如实写明偏差数值与两条原因（leg-A 近似——只走 A 腿价格、B 腿未建模；未移植 portfolio 暂停状态机），不算失败。
5. `$PY -m pytest validation/ _shared/ -q` 仍绿（基线 142 passed, 3 skipped）。

**est**: 28 min | **machine**: mac（数据缓存在仓库内，无需 /home/smark 源） | **deps**: T4（同目录 adapter 已删；文件不交叉，顺序保证目录干净）、T8（verdict 含 fee_shock 字段——T13 文档要引用；T12 本身不依赖其代码）、T1 可选（backtrader leg 缺失时 harness 记 skip 不崩）。

---

## T13 — quant-loop/docs/onboarding-validation.md（新策略 5 分钟接入文档）

**目标**：新建一页纸 quickstart 文档，让零上下文的人照做即可：scaffold → 写 signals.py → 一条命令跑 7 窗双框架 CV + 60bps fee shock → 读懂 verdict.json → 对照门禁表 → 避开常见坑。

**Reads**（写文档时的事实来源，均已核实）：
- `quant-loop/validation/gates.py:8-17` — 门禁表权威定义（**照抄这个，不要抄 validation/README.md，它口径是旧的**）：
  G1 全期 mean Sharpe ≥ 1.0；G2 min（全期年化， mean OOS 年化） ≥ 15%；G3 max_drawdown > -0.25（负号约定）；G4 profit_factor > 1.5；G5 框架 CV（backtrader/freqtrade）mean OOS Sharpe ≥ 1.0；G6 bootstrap 95% CI lower ≥ 0.5（10000 次 resample，seed=42）；G7 Deflated Sharpe Ratio > 0（Bailey-LdP 2014，n_trials=100）；T1 pooled OOS trades ≥ 30。任一 FAIL 即 BLOCK；缺字段（如框架引擎没装导致 G5 无数据）记 MISSING_FIELD = FAIL。
- `quant-loop/validation/oos_harness.py:1-20` — CLI 用法与 exit code 语义：0=全 PASS，1=有 FAIL，2=harness error。
- `quant-loop/validation/generic_harness.py:265-272, 356-358` — verdict.json 结构：`variant / pipeline("generic") / timeframe / windows[] / symbols{sym}{window_label}{framework: metrics} / framework_skips{} / full_native / gates[] / verdict`；T8 完成后顶层还有 `fee_shock`（含 60bps 档 Sharpe 与 `passed_60bps` 布尔）。
- `quant-loop/_shared/templates/example_strategy.py` — contract-v2 最小示例（双均线/channel breakout toy，可引用其 `generate_signals(bars, config)` 形态）。
- `quant-loop/strategies/pairs_cointegration_1d_20260709/signals.py` — T12 产出的真实迁移样板（文档里作为"真实策略长什么样"的参照；多 symbol/pairs 策略如何识别当前 symbol 的 `_identify_symbol` 模式值得点名）。
- `quant-loop/scripts/new_variant.py` — T10 产出的 scaffold 工具。**若 T10 尚未落地（文件不存在），文档相应段落改写为手动三步**：建 `strategies/<name>_<yyyymmdd>/` → 放 `config.json`（必含 `instruments`、`timeframe`、`fees_bps_per_side`、`slippage_bps_per_side`、`starting_capital_usd`）→ 放 `data_loader.py`（暴露 `load_all(symbols, timeframe)`）→ 放 `signals.py`（暴露 `generate_signals(bars, config)` 与 `DEFAULT_CONFIG`），并注明"scaffold 工具落地后此段以 `scripts/new_variant.py` 为准"。

**Writes**：`quant-loop/docs/onboarding-validation.md`（仅此一个文件；`quant-loop/docs/` 已存在，目前只有 `decisions/` 子目录）。

**文档必须包含的段落**：
1. **TL;DR 五条命令**：scaffold（或手动三步）→ 写信号 → `$PY -m validation.oos_harness --variant <name> --frameworks native,backtrader,freqtrade --windows 7`（T8 后默认即 7 窗，文档统一写 7）→ 看 `results/validation/verdict.json` → exit code 语义表（0/1/2）。
2. **contract v2 契约**：`generate_signals(bars, config) -> list[Trade]`；`Trade` 四字段（`_shared/run_backtest.py:77-87`）；entry/exit 必须落在 bars.index 上，off-bar 会被**静默跳过**（generic_harness.py:144-145）；`DEFAULT_CONFIG` 必须存在（contract 合成 smoke 用）；equity/metrics 一律由引擎算，策略禁止自己算 Sharpe。
3. **verdict.json 字段解释表** + `framework_skips` 的含义（引擎没装→该腿 skip→G5 MISSING_FIELD=FAIL，不是 PASS）。
4. **G1-G7+T1 阈值表**（按上面 gates.py 口径）+ fee shock 60bps 档说明。
5. **常见坑**（每条一句话）：off-bar 交易被 skip 不报错；框架引擎未安装时 G5 是 FAIL 不是 skip-pass；`signals.py` 函数体内不得 lazy import 本目录模块（`_load_module` import 后移除 sys.path，native_engine.py:47-51）；1d 以下 timeframe 的 freq_per_year 映射表（generic_harness.py:61-71）；多 symbol/pairs 策略的 `_identify_symbol` 模式（指向 T12 样板）。
6. **实测记录**：下面验收步骤 1-3 的命令 + 实际 exit code，原样贴进文档（"以下命令于 2026-07-25 在 pairs_cointegration_1d_20260709 上实测"）。

**步骤**：
1. 跑 `$PY -m validation.oos_harness --variant pairs_cointegration_1d_20260709 --frameworks native --windows 2; echo exit=$?`（用 2 窗控制时长，文档里说明生产用 7 窗），记录 exit code。
2. 跑 `$PY -c "import json; r=json.load(open('strategies/pairs_cointegration_1d_20260709/results/validation/verdict.json')); print(r['pipeline'], r['verdict'], sorted(r.keys()))"`，把输出贴进文档字段解释段。
3. 若 T10 已落地：跑 `cd quant-loop && $PY scripts/new_variant.py onboard_doc_smoke --timeframe 1h --symbols BTCUSDT` 验证 scaffold 命令可行，记录 exit code 后**删除生成的 smoke 目录**；若未落地，跳过并在文档注明。
4. 按上面 6 段结构写文档。

**Acceptance**：
1. `test -f quant-loop/docs/onboarding-validation.md`。
2. `grep -c "fee_shock" quant-loop/docs/onboarding-validation.md` ≥ 1；`grep -c "Bonferroni" quant-loop/docs/onboarding-validation.md` == 0（只许出现 "Deflated Sharpe"，不许复活旧口径）；`grep -c "Deflated Sharpe" ...` ≥ 1。
3. `grep -cE "exit(code)?[= ]" quant-loop/docs/onboarding-validation.md` ≥ 1（文档含实测 exit code 记录）。
4. 文档内出现的每条命令都必须在任务内真实执行过（步骤 1-3），exit code 与文档所写一致。

**est**: 20 min | **machine**: mac | **deps**: T12（样板目录与 verdict.json 必须存在）、T8（fee_shock 字段）、T10（可选，缺失时走手动三步 fallback）。

---

## T14 — W1 全链验收串联脚本（T1→T13 一条命令）

**目标**：新建 `quant-loop/scripts/w1_acceptance.sh`，W1 全部 13 个任务完成后，任何人跑 `bash quant-loop/scripts/w1_acceptance.sh` 即可得到逐条 PASS/FAIL 清单 + 总结论（脚本本身恒 exit 0，结论看输出，避免 `set -e` 在中途掐断报告）。

**Reads**：无需读码；下列检查项的事实来源已在本文件内联（对应 round1 T1-T13 的验收命令）。

**Writes**：`quant-loop/scripts/w1_acceptance.sh`（仅此一个文件；`quant-loop/scripts/` 已存在）。

**脚本内容（照此写，逐项一个 check 函数，输出 `[PASS]/[FAIL] Tn — 描述`）**：

```bash
#!/bin/bash
# W1 (backtest/validation engine unification) end-to-end acceptance.
# Run from anywhere: bash quant-loop/scripts/w1_acceptance.sh
# Always exits 0; read the per-task PASS/FAIL lines and the final tally.
QL="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY=/Users/mark/sdk/mamba-envs/trading/bin/python3
cd "$QL" || exit 1
fails=0
ck() { # ck <id> <desc> <cmd...>
  local id="$1" desc="$2"; shift 2
  if "$@" >/tmp/w1_acc_$$.log 2>&1; then echo "[PASS] $id — $desc"
  else echo "[FAIL] $id — $desc (see /tmp/w1_acc_$$.log)"; fails=$((fails+1)); fi
}
ck T1  "framework engines importable" \
  $PY -c "import backtrader, vectorbt; import freqtrade"   # freqtrade 装不上时把本行拆成两条 ck：backtrader+vectorbt 必须 PASS，freqtrade 记 WARN 不算 FAIL
ck T2  "fee_shock module tests" $PY -m pytest _shared/validation/test_fee_shock.py -q
ck T34 "no framework_adapter_*.py left anywhere" \
  bash -c 'test "$(find strategies -name "framework_adapter_*.py" -not -path "*__pycache__*" | wc -l | tr -d " ")" = "0"'
ck T5  "README gate wording fixed" \
  bash -c 'test "$(grep -c Bonferroni validation/README.md)" = "0" && test "$(grep -c "Deflated Sharpe" validation/README.md)" -ge 1'
ck T6  "no /home/smark in _shared+validation" \
  bash -c 'test "$(grep -rl /home/smark _shared validation --include="*.py" | wc -l | tr -d " ")" = "0"'
ck T7  "engine perf budget" $PY -m pytest _shared/test_run_backtest_perf.py -q
ck T8  "generic harness fee shock + 7-window default tests" \
  $PY -m pytest validation/test_generic_harness.py -q
ck T9  "gates + metrics tests" \
  $PY -m pytest validation/test_gates.py _shared/validation/test_compute_metrics.py validation/test_metrics.py -q
ck T10 "scaffold tool smoke" bash -c '
  d=$('$PY' scripts/new_variant.py w1acc_smoke --timeframe 1h --symbols BTCUSDT >/dev/null 2>&1 && ls -d strategies/w1acc_smoke_* 2>/dev/null | head -1)
  test -n "$d" && '$PY' -m validation.oos_harness --variant "$d" --frameworks native --windows 2 >/dev/null 2>&1
  rc=$?; rm -rf "$d"; test $rc -le 1'
ck T11 "CI script bash-3.2 compatible" \
  bash -c 'bash -n validation/ci/validate_changed_variants.sh && /bin/bash validation/ci/validate_changed_variants.sh HEAD HEAD | grep -q "no strategy variant changes"'
ck T12 "pairs_cointegration_1d on generic pipeline" bash -c '
  '$PY' -m validation.oos_harness --variant pairs_cointegration_1d_20260709 --frameworks native --windows 2 >/dev/null 2>&1; rc=$?
  test $rc -le 1 && '$PY' -c "import json; assert json.load(open(\"strategies/pairs_cointegration_1d_20260709/results/validation/verdict.json\"))[\"pipeline\"]==\"generic\""'
ck T13 "onboarding doc exists with fee_shock + Deflated Sharpe" \
  bash -c 'test -f docs/onboarding-validation.md && test "$(grep -c fee_shock docs/onboarding-validation.md)" -ge 1'
ck BASE "baseline suites still green" $PY -m pytest validation/ _shared/ -q
echo "----"; if [ $fails -eq 0 ]; then echo "W1 ACCEPTANCE: ALL PASS"; else echo "W1 ACCEPTANCE: $fails FAIL"; fi
exit 0
```

写脚本时注意：`$PY` 在 `bash -c '...'` 内层单引号里不可见，所以上面用 `'$PY'` 拼接（外层双引号展开）——成卡时可以改为在每个 `bash -c` 里重新定义 `PY=...`，怎么稳怎么来，但**每条检查的命令语义不得变**。T1 那行按注释拆成两条（backtrader+vectorbt 硬 PASS，freqtrade 软 WARN）。

**步骤**：1. 写文件；2. `bash -n quant-loop/scripts/w1_acceptance.sh` 语法检查；3. 若 T1-T13 已全部落地，实跑一次并把输出贴进任务结果；若未全部落地，只交脚本 + `bash -n` 结果，并明确标注"未实跑，待 T1-T13 完成后由 parent 执行"。

**Acceptance**：`bash -n quant-loop/scripts/w1_acceptance.sh` exit 0；`grep -c "^ck " quant-loop/scripts/w1_acceptance.sh` ≥ 14；脚本不含 `set -e`（`grep -c "set -e" ...` == 0）。

**est**: 15 min | **machine**: mac（实跑需 mac 上的 trading env + 仓库数据） | **deps**: T1–T13 全部（实跑前提下；脚本本身无 deps）。

---

## Cross-slice conflicts（给 parent）

1. **T12 vs T4（同目录）**：T4 删 `strategies/pairs_cointegration_1d_20260709/framework_adapter_freqtrade.py` 及 graveyard adapter；T12 在该目录**新建** `signals.py`、**改** `data_loader.py`。文件不交叉，但必须 T4 先行（round1 已排 G0→G2），否则 T12 的验收 `find ... framework_adapter_*.py == 0`（在 T14 里）会被未删的 adapter 打破。
2. **T12 改 `data_loader.py` 的 `load_all` 签名**：若 data workstream（W3）也在动策略目录的 data_loader 或统一 `load_all(symbols, timeframe)` 契约，需划界——本卡只改 `pairs_cointegration_1d_20260709/data_loader.py` 一个文件。
3. **T13 vs T5（文档口径）**：T5 修 `validation/README.md` 门禁表；T13 写新文档。两者引用同一权威（`validation/gates.py:8-17`），无文件交叉，但若 T5 未先落地，T13 不得从旧 README 抄口径（本卡已内联正确口径，可独立执行）。
4. **T14 的 T10 检查项会创建并删除 smoke 策略目录**（`strategies/w1acc_smoke_*`）——若与 T10 自身验收并行跑，目录名前缀不同（T10 用 `sprint_smoke_*`），不冲突。
5. **已知验收口径软化**（相对 round1）：T12 的 "native leg OOS Sharpe 与 legacy 偏差 <5%" 在 leg-A 单币种近似下大概率达不到（legacy 3.597 是组合口径）。本卡把硬验收改为"pipeline=generic + exit∈{0,1} + BTC-ETH entry-date overlap ≥60%"，Sharpe 偏差如实上报。若 parent 坚持 5% 口径，T12 需要改设计（如组合级 equity 注入），超出 30 min 预算，建议接受软化。
