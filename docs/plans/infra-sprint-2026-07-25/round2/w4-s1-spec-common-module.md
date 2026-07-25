# w4-s1 — W4/T01-T02 细化：预注册 SPEC + `se_h3_common.py` 公共模块（round-2 执行卡）

- slice: `w4-s1`（父 workstream: `w4-signal-enhance-h3`）
- 日期: 2026-07-25
- 本 slice 只含 2 张卡：**T01**（预注册 SPEC 文档）与 **T02**（公共 import 模块 `se_h3_common.py`）。
  二者无相互依赖，可并行；同 wave 另有 T03/T04（别的 slice 细化），文件不相交。
- 全局约束（两张卡共用，执行 agent 必读）:
  - python 一律 `/Users/mark/sdk/mamba-envs/trading/bin/python3`（系统 python3 没有 pyarrow）。
  - 只准在 `quant-loop/research/swarm/2026-07-25/signal-enhance-h3/full_history/`（下称 `FH`，当前**尚不存在**，由本 slice 任务创建）下新建文件。
  - 只读 import，**禁止修改**: `quant-loop/strategies/_indicators/mtf_xs_pairs_base_20260718.py`、`quant-loop/strategies/mtf_xs_pairs_1m_15m_2h_h3_20260718/config.json`、`quant-loop/research/swarm/2026-07-25/H3-variants-h1h2h4/run_btcsol_variants_fixed.py`、`quant-loop/data/perp_1m/*.parquet`、`quant-loop/data/funding/*.parquet`、`quant-loop/research/swarm/2026-07-25/H3-baseline-repro/metrics.json`、`signal-enhance-h3/` 目录下一切既有产物。
  - 禁止任何 git 操作；worktree 里有别人的未提交改动，不许碰。
  - 本 slice 不跑回测（T02 验收只做数据加载，不调用 `run_backtest`）。

---

## 背景（已逐行核实的代码事实，两张卡共用）

**权威管线 = `run_btcsol_variants_fixed.py`**（`/Users/mark/multica/quant-loop/research/swarm/2026-07-25/H3-variants-h1h2h4/run_btcsol_variants_fixed.py`，504 行）。可 import 的顶层函数（行号为 2026-07-25 实地读取值）:

| 函数 | 行 | 作用 |
|---|---|---|
| `load_perp_1m(symbol)` | L90 | 读 `quant-loop/data/perp_1m/{symbol}_1m.parquet` → tz-naive DatetimeIndex(`openTime`) 的 OHLCV DataFrame |
| `load_funding(symbol)` | L101 | 读 `quant-loop/data/funding/{symbol}.parquet`（`ts`+`fundingRate` 列）→ tz-naive Series |
| `align_and_clip(d1m, funding)` | L112 | 对齐公共索引并裁到 funding 可得区间 |
| `load_config(hyp)` | L135 | 读 `strategies/mtf_xs_pairs_1m_15m_2h_{hyp}_20260718/config.json`，强制 `instruments=["BTCUSDT","SOLUSDT"]`、`pairs=["BTCUSDT/SOLUSDT"]`、fee/slip 各 1bps |
| `portfolio_metrics(result, idx, cfg)` | L220 | compute_metrics + daily-resampled Sharpe + PF/MDD |
| `walk_forward_oos(d1m, funding, cfg, fee, slip)` | L242 | expanding-train 7 窗 OOS（train 525600 / test 262800 / step 262800） |
| `fee_shock_metrics(equity, trades, pair_rt_bps)` | L313 | gross daily equity 上按 exit 日扣 `(rt_bps/1e4)*0.005`/笔 |

import 该模块的副作用（安全，已核）：`RESULTS_DIR.mkdir(exist_ok=True)`（L55，目录已存在）、向 `sys.path` 插入 strategies/_indicators/_shared 等 5 条路径（L63-66）、`warnings.filterwarnings("ignore")`（L77）。**不会在 import 时跑回测**（`main()` 只在 `__main__` 下，L503）。

**⚠️ funding 双源问题（SPEC 必须写明的核心警示）**：2024 子样本证据（Sharpe 8.07）用的 funding 不是权威源——
`signal-enhance-h3/data_loader_patch.py` L19-20：BTC funding 读 `quant-loop/funding_analysis/BTCUSDT_funding.parquet`，SOL funding 读 `quant-loop/strategies/_graveyard/xs_pairs_30m/vpvr_xs_pairs_30m_funding_filter_20260712/data/SOLUSDT__funding.parquet`；而权威管线读 `quant-loop/data/funding/{BTC,SOL}USDT.parquet`（fixed runner L59-60/L101-109）。fund_allow 过滤（2h funding EMA < 5e-4）在两套源下会不同 ⇒ **8.07 不可外推，全历史必须用权威 loader 重跑**。1m klines 两边同源（`data/perp_1m/`，dlp L18 vs fixed runner L59），仅 funding 不同。

**baseline 锚点**（`H3-baseline-repro/metrics.json`，已用 trading python 实地读出）:
- `n_bars = 2 448 219`，`data_span = 2021-11-20 16:01:00 → 2026-07-17 19:39:00`
- full-history `n_trades = 40 963`；OOS Sharpe **1.8748**；bootstrap CI lower **0.8879**；60bps fee-shock Sharpe **-0.0213**
- window 0 test 区间 `2022-11-20 16:01:00 → 2023-05-22 04:00:00`，Sharpe 1.7252（7 窗全表见 round1 `w4-signal-enhance-h3.md` §1.5）

**2024 子样本候选参数**（`signal-enhance-h3/quick_verify.py` L42）：
`{"slope_filter": {"lookback": 4, "sign": "favorable"}, "adverse_stop_z": 0.7, "regime_break": 9.0}`，
变体名 `slope_fav_4_stop_0_7`，2024 结果（`quick_verify_2024.json`）：704 trades、Sharpe 8.07（组合行在 json 第 5 个对象）。

**假设依据**（`signal-enhance-h3/SUMMARY.md` L28/L32）：2024 baseline 78.2% 的交易以 `regime_break` 出场、均亏 -19.7bps net；mean-revert 出场的 21% 均赚 +39.9bps。增强逻辑 = 顺向 15m z-slope 过滤入场 + 0.7z 逆势止损替代宽 regime_break 止损。

**H3 config 锁定参数**（`strategies/mtf_xs_pairs_1m_15m_2h_h3_20260718/config.json`，只读）：
z_entry 2.5（L17）、z_exit 0.5（L18/L30）、max_hold 240（L20/L31）、regime_break 默认 3.0（L19/L31）、
funding_filter_threshold 5e-4（L21）、funding_ema_window 4（L22）、atr_normalize_window 2016（L23）、
walk_forward train/test/step = 525600/262800/262800（L45-47）、bootstrap seed 42 / 10000 次（L56-57）。

---

## T01 — 预注册 SPEC 文档（写死假设/参数/证伪条件）

- **目标（一句话）**：在跑任何全历史回测之前，把候选假设、锁定参数、证伪条件落成不可事后修改的 SPEC 文档。
- **机器**: mac（纯写作，无数据依赖）| **预估**: 15 min | **依赖**: 无
- **读（全部只读，用于引用事实）**:
  - `/Users/mark/multica/quant-loop/research/swarm/2026-07-25/signal-enhance-h3/SUMMARY.md`（L28/L32 的 78.2% regime_break 统计，L97-107 的 full-history 验证要求）
  - `/Users/mark/multica/quant-loop/research/swarm/2026-07-25/signal-enhance-h3/quick_verify.py` L37-45（候选参数）
  - `/Users/mark/multica/quant-loop/research/swarm/2026-07-25/signal-enhance-h3/quick_verify_2024.json`（第 5 个对象 = `slope_fav_4_stop_0_7` 的 2024 指标）
  - `/Users/mark/multica/quant-loop/research/swarm/2026-07-25/signal-enhance-h3/data_loader_patch.py` L19-20（funding 双源证据）
  - `/Users/mark/multica/quant-loop/strategies/mtf_xs_pairs_1m_15m_2h_h3_20260718/config.json`（锁定参数值）
- **写（新建，唯一产出）**:
  - `/Users/mark/multica/quant-loop/research/swarm/2026-07-25/signal-enhance-h3/full_history/SPEC_signal_enhance_h3_fullhist.md`
  - 先 `mkdir -p` 创建 `full_history/`（该目录当前不存在）。
- **步骤**:
  1. 读上面 5 个文件，核对本卡「背景」节数值与文件一致（不一致以文件为准并在 SPEC 注明）。
  2. 写 SPEC，**必须包含以下小节，标题逐字使用**（grep 验收按这些字符串）:
     - `# SPEC — signal-enhance-h3 full-history validation (pre-registered 2026-07-25)`
     - `## 假设` — 一句话：「15m z-slope 顺向转弯入场过滤（lookback=4, favorable）+ 0.7z 逆势止损（替代 regime_break=3.0 宽止损，等效设为 9.0）能过滤掉以 regime_break 出场的亏损交易（2024 子样本中占 78.2%、均亏 -19.7bps net），使 H3 BTC+SOL 组合在全历史 walk-forward OOS 上保持 Sharpe ≥ 1.0 且 60bps 成本下仍为正。」
     - `## 锁定参数` — 逐项列出且**禁止改动**：z_entry=2.5、z_exit=0.5、max_hold=240、slope_lookback=4、slope_sign=favorable、adverse_stop_z=0.7、regime_break=9.0、fee=1bps+slip=1bps per side per leg（4bps pair RT）、标的 BTCUSDT+SOLUSDT only、funding filter threshold=5e-4 / ema_window=4、walk_forward train/test/step=525600/262800/262800（7 窗）、bootstrap seed=42 / resamples=10000。注明出处：quick_verify.py L42 + H3 config.json。
     - `## 证伪条件` — 任一成立即 KILL 证据成立（判定回主线，不在本 workstream 下结论）:
       1. 7 窗 OOS mean Sharpe（daily-resampled）< 1.0；或
       2. bootstrap CI lower（seed 42, 10000 次）< 0.5；或
       3. 60bps pair-RT fee-shock Sharpe ≤ 0；或
       4. parity 测试（后续卡 T05/T06）不通过 ⇒ 管线本身不可信，全部结果作废。
       附 baseline 锚点对照（H3 baseline：OOS Sharpe 1.8748 / CI lower 0.8879 / 60bps Sharpe -0.0213 / full n_trades 40963）。
     - `## funding 双源警示` — 必须写明：2024 子样本证据（704 trades, Sharpe 8.07）的 funding 来自 `funding_analysis/BTCUSDT_funding.parquet` 与 graveyard 的 `SOLUSDT__funding.parquet`（data_loader_patch.py L19-20），**不是**权威源 `data/funding/{BTC,SOL}USDT.parquet`；fund_allow 在两套源下不一致 ⇒ 8.07 仅为方向性证据，不得作为决策依据；本次全历史验证一律使用权威 loader（fixed runner 的 `load_funding`）。若其他 workstream 在做数据层统一，注意旧数与新管线结果不可直接比，差异来自 funding 源而非策略逻辑。
     - `## 范围与纪律` — 只新增 `full_history/` 下文件；不改生产/共享代码；不做参数扫荡（cycle-46 纪律：仅此一个预注册组合）；不跑 G5/G7（标 NOT_RUN）；KEEP/KILL 判决归主线单线程。
  3. 文末附「预注册哈希占位」一行：`Pre-registration commit: (to be filled by orchestrator at dispatch time)`。
- **验收**（mac，在仓库根跑）:
  ```bash
  FH=/Users/mark/multica/quant-loop/research/swarm/2026-07-25/signal-enhance-h3/full_history
  test $(grep -c "adverse_stop_z" $FH/SPEC_signal_enhance_h3_fullhist.md) -ge 1 \
    && grep -q "## 证伪条件" $FH/SPEC_signal_enhance_h3_fullhist.md \
    && grep -q "## funding 双源警示" $FH/SPEC_signal_enhance_h3_fullhist.md \
    && grep -q "regime_break=9.0" $FH/SPEC_signal_enhance_h3_fullhist.md \
    && grep -q "0.8879" $FH/SPEC_signal_enhance_h3_fullhist.md \
    && echo T01-OK
  ```
  预期输出 `T01-OK`。

---

## T02 — 公共模块 `se_h3_common.py`（权威管线 import + sys.path 装配）

- **目标（一句话）**：提供全 slice 唯一的数据/config 入口——从 fixed runner 原位 import 权威函数（杜绝复制漂移），并暴露带锁定覆盖参数的 H3 config。
- **机器**: mac（数据 parquet 在 Mac 本地）| **预估**: 20 min（含验收的数据加载 ~1-2 min）| **依赖**: 无（与 T01 并行）
- **读（只读）**:
  - `/Users/mark/multica/quant-loop/research/swarm/2026-07-25/H3-variants-h1h2h4/run_btcsol_variants_fixed.py`（import 源；函数行锚见背景表）
  - `/Users/mark/multica/quant-loop/strategies/mtf_xs_pairs_1m_15m_2h_h3_20260718/config.json`（H3 参数，由 `load_config("H3")` 间接读）
- **写（新建，唯一产出）**:
  - `/Users/mark/multica/quant-loop/research/swarm/2026-07-25/signal-enhance-h3/full_history/se_h3_common.py`
  - 若 T01 尚未创建 `full_history/` 目录，本任务先 `mkdir -p`。
- **步骤**:
  1. 新建 `se_h3_common.py`，结构按下面骨架**逐字实现**（这是完整规格，不是示意）:
     ```python
     """Common plumbing for the signal-enhance-h3 full-history validation.

     Single import point for the authoritative pipeline: everything data- or
     config-related comes from ../H3-variants-h1h2h4/run_btcsol_variants_fixed.py
     (bit-identical to the H3 baseline methodology) so no code is copied and
     cannot drift. Read-only with respect to production/shared code.
     """
     from __future__ import annotations

     import sys
     from pathlib import Path

     FH_DIR = Path(__file__).resolve().parent                       # full_history/
     VARIANTS_DIR = FH_DIR.parent.parent / "H3-variants-h1h2h4"     # sibling swarm dir

     for p in (str(VARIANTS_DIR),):
         if p not in sys.path:
             sys.path.insert(0, p)

     from run_btcsol_variants_fixed import (  # noqa: E402
         align_and_clip,        # L112
         fee_shock_metrics,     # L313
         load_config,           # L135
         load_funding,          # L101
         load_perp_1m,          # L90
         portfolio_metrics,     # L220
     )

     SYMBOLS = ("BTCUSDT", "SOLUSDT")

     # Locked enhancement parameters (pre-registered, see SPEC_signal_enhance_h3_fullhist.md).
     SE_H3_SLOPE_LOOKBACK = 4
     SE_H3_SLOPE_SIGN = "favorable"
     SE_H3_ADVERSE_STOP_Z = 0.7
     SE_H3_REGIME_BREAK = 9.0  # effectively disables the wide regime_break stop


     def load_aligned_data():
         """Authoritative BTC+SOL 1m klines + funding, aligned & clipped (baseline method).

         Returns (d1m, funding, common_idx): dicts keyed by symbol plus the
         common tz-naive DatetimeIndex. Expected: 2448219 bars,
         2021-11-20 16:01 -> 2026-07-17 19:39.
         """
         d1m = {s: load_perp_1m(s) for s in SYMBOLS}
         funding = {s: load_funding(s) for s in SYMBOLS}
         d1m, funding = align_and_clip(d1m, funding)
         return d1m, funding, d1m["BTCUSDT"].index


     def load_se_h3_config() -> dict:
         """H3 config via the authoritative loader + locked se-h3 overrides."""
         cfg = load_config("H3")  # already forces BTC+SOL + 1bps/1bps cost model
         cfg["exit"]["regime_break_threshold"] = SE_H3_REGIME_BREAK
         cfg["indicators"]["regime_break_threshold"] = SE_H3_REGIME_BREAK
         cfg["se_h3"] = {
             "slope_lookback": SE_H3_SLOPE_LOOKBACK,
             "slope_sign": SE_H3_SLOPE_SIGN,
             "adverse_stop_z": SE_H3_ADVERSE_STOP_Z,
             "regime_break": SE_H3_REGIME_BREAK,
         }
         return cfg
     ```
  2. 路径推导说明：`FH_DIR` = `…/swarm/2026-07-25/signal-enhance-h3/full_history/`；`FH_DIR.parent.parent` = `…/swarm/2026-07-25/`；拼上 `H3-variants-h1h2h4` 即 fixed runner 所在目录。加入 `sys.path` 后 `import run_btcsol_variants_fixed` 会顺带把 strategies/_indicators/_shared 路径装好（fixed runner L63-66），本模块不需要再装。
  3. 不要 import `run_backtest`、`walk_forward_oos`（本 slice 用不到，留给后续卡的模块自己 import）；不要把 `matplotlib` import 进来以外的任何绘图依赖拉进本模块（fixed runner 自身 import matplotlib 属既定副作用，忽略）。
  4. `__all__` 不必定义（后续卡 T04 的验收只看它自己的模块）。
- **验收**（mac，`cd` 到 FH 目录后跑，必须逐条通过）:
  ```bash
  cd /Users/mark/multica/quant-loop/research/swarm/2026-07-25/signal-enhance-h3/full_history
  PY=/Users/mark/sdk/mamba-envs/trading/bin/python3
  # (1) import 无副作用、六个权威函数可到达
  $PY -c "import se_h3_common as c; assert callable(c.load_perp_1m) and callable(c.fee_shock_metrics); print('import-OK')"
  # (2) 数据加载锚点（耗时 ~1-2 min，属正常）
  $PY -c "
  import se_h3_common as c
  d, f, i = c.load_aligned_data()
  assert len(i) == 2448219, len(i)
  assert str(i[0]) == '2021-11-20 16:01:00', i[0]
  assert str(i[-1]) == '2026-07-17 19:39:00', i[-1]
  assert set(d) == {'BTCUSDT','SOLUSDT'} and set(f) == {'BTCUSDT','SOLUSDT'}
  print('data-OK', len(i), i[0], i[-1])
  "
  # (3) config 锁定覆盖
  $PY -c "
  import se_h3_common as c
  cfg = c.load_se_h3_config()
  assert cfg['hypothesis'] == 'H3'
  assert cfg['instruments'] == ['BTCUSDT','SOLUSDT'] and cfg['pairs'] == ['BTCUSDT/SOLUSDT']
  assert cfg['fees_bps_per_side'] == 1.0 and cfg['slippage_bps_per_side'] == 1.0
  assert cfg['exit']['regime_break_threshold'] == 9.0
  assert cfg['indicators']['zscore_entry_threshold'] == 2.5
  assert cfg['se_h3'] == {'slope_lookback': 4, 'slope_sign': 'favorable', 'adverse_stop_z': 0.7, 'regime_break': 9.0}
  assert cfg['walk_forward']['train_bars_1m'] == 525600
  print('config-OK')
  "
  ```
  预期三行输出：`import-OK`、`data-OK 2448219 2021-11-20 16:01:00 2026-07-17 19:39:00`、`config-OK`。
  若 `len(i) != 2448219`：说明上游 parquet 数据漂移，**不要改断言凑数**，原样报告实际值并停止。

---

## 冲突与交接备注（给其他 slice / 主线）

- **T03/T04 依赖 T02 的接口**：`load_aligned_data()` 返回三元组 `(d1m, funding, common_idx)`；`load_se_h3_config()` 返回的 cfg 含 `cfg["se_h3"]` 子字典（四个锁定参数）且 `cfg["exit"]["regime_break_threshold"]=9.0`。其他 slice 的卡应按此接口写，不要改。
- **冻结要求（与 round1 §5 一致）**：T02 的验收锚点（2448219 行、span、config 字段）依赖上游文件字节级稳定；任何清理/重构类 workstream（_shared 收敛、路径替换）在 sprint 期间不得动 `run_btcsol_variants_fixed.py`、H3 `config.json`、`data/perp_1m/`、`data/funding/`。
- **funding 双源**：凡引用 2024 旧数（Sharpe 8.07、704 trades）的卡必须保留「不同 funding 源、不可直接比」的警示，已在 T01 SPEC 落死。
- T02 验收第 (2) 条会占 1 核 ~1-2 min 做 parquet 加载；与同 wave 任务无 CPU 冲突（不跑回测）。
