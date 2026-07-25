# w4-s3 — W4/T05-T06 质量闸门：信号 parity + 循环 parity 双锚定（round-2 执行卡）

- 日期: 2026-07-25
- 范围: 只写 `quant-loop/research/swarm/2026-07-25/signal-enhance-h3/full_history/`（下文 `FH`）下各 1 个测试文件 + 失败时的诊断 dump（`FH/results/`）。
- 定位: 这是 W4 的**质量闸门**。两卡不全绿，wave 3+（T07 全历史、T08-T14 WF 窗口、T15 聚合）一律不得放行——parity 不绿后面全是垃圾数。
- 通用约束:
  - python 一律 `/Users/mark/sdk/mamba-envs/trading/bin/python3`（默认 python3 没有 pyarrow）。
  - **不做 git 操作；不改任何既有文件**；生产/共享代码只读 import。
  - worktree 有别人的未提交改动，一律不碰。

## 已验证的代码锚点（round-2 逐行核对过，卡片可直接引用）

| 锚点 | 位置 |
|---|---|
| 权威 loader `load_perp_1m` / `load_funding` / `align_and_clip` | `quant-loop/research/swarm/2026-07-25/H3-variants-h1h2h4/run_btcsol_variants_fixed.py` L90-98 / L101-109 / L112-128 |
| quick_verify 数据补丁（funding 非同源：BTC 走 `funding_analysis/BTCUSDT_funding.parquet`，SOL 走 graveyard parquet） | `quant-loop/research/swarm/2026-07-25/signal-enhance-h3/data_loader_patch.py` L16-20（**L16 ROOT 硬编码 `/Users/mark/multica/quant-loop` → 只能在 mac 跑**） |
| 参考信号链 `enhance_signals`（`z_slope_4` 构造 L70-73：pair z → `aggregate_ohlcv(...,"15min")` → `zscore_slope(z_15m, 4)` → `align_lower_to_upper`） | `signal-enhance-h3/run_experiments.py` L54-91 |
| 参考回测循环 `backtest_variant`（favorable 过滤 L182-190；adverse stop L233-237；exit 顺序 `z_mean_revert → regime_break → adverse_stop → max_holding` L229-239；成本 `2*2*(fee+slip)/1e4` L159） | `signal-enhance-h3/run_experiments.py` L115-280 |
| quick_verify 候选参数 `{"slope_filter":{"lookback":4,"sign":"favorable"},"adverse_stop_z":0.7,"regime_break":9.0}` | `signal-enhance-h3/quick_verify.py` L42 |
| 2024 锚点值：`slope_fav_4_stop_0_7` = **704 trades, Sharpe 8.0735**, mean_net 15.429bps, win 68.89%, PF 1.0873 | `signal-enhance-h3/quick_verify_2024.json` L47-55（与 quick_verify.log 一致） |
| 2024 切片 bar 数 **525601**（`slice_by_date(start="2024-01-01", end="2024-12-31")`，闭区间 mask） | `signal-enhance-h3/quick_verify.log` 首行；`data_loader_patch.py` L60-79 |
| base 引擎 `_backtest_pair`（entry L494-554，fund_allow 检查 L522-525，exit if/elif 链 L562-568，成本 L576，trade 键 `pnl_pct`） | `quant-loop/strategies/_indicators/mtf_xs_pairs_base_20260718.py` L463-605 |
| base trade dict = dataclass asdict，`entry_ts`/`exit_ts` 被转 **ISO 字符串** | base L178-202（`_trade_dict`） |
| `build_h3_signals`（z L342、fund_allow L345-361、size_scale L363-369） | base L318-381 |
| `sharpe_daily_resampled(bar_return, index)` | base L760 |
| H3 config（z_entry 2.5 / z_exit 0.5 / regime_break 3.0 / max_hold 240 / funding_thr 5e-4 / ema 4 / atr_norm 2016 / fee 1+1bps） | `quant-loop/strategies/mtf_xs_pairs_1m_15m_2h_h3_20260718/config.json` L15-42 |

## 跨卡接口契约（T06 对 T04 产物 `FH/se_h3_loop.py` 的硬性要求）

T04 属另一 slice，但 T06 依赖其函数签名。契约如下（若实际签名不符，T06 执行者**不得自行改 se_h3_loop.py**，直接 STOP 并报「接口不一致」）：

```python
def backtest_pair_se(signals: dict, pair: str, sizing_scale=None,
                     fee_bps: float = 1.0, slip_bps: float = 1.0,
                     slope=None, adverse_stop_z=None, regime_break: float = 3.0) -> dict:
```

- `slope`: `pd.Series` 或 `None`。非 None 时按 **favorable** 约定过滤：direction=+1 要求 slope>0，=-1 要求 slope<0，NaN 拒入（语义 = `run_experiments.py` L182-190）。
- `adverse_stop_z`: float 或 None。非 None 时 exit 链在 regime_break 之后、max_holding 之前插入 adverse_stop（语义 = L233-237）。
- exit 链顺序固定 `z_mean_revert → regime_break → adverse_stop → max_holding`。
- 返回 `{"pair","trades","bar_return","n_bars"}`；每个 trade dict 至少含键：`direction, entry_ts, exit_ts, entry_price_a, entry_price_b, exit_price_a, exit_price_b, pnl_pct(净), gross_pct, bars_held, z_at_entry, z_at_exit, exit_reason`（成本口径 `2*2*(fee+slip)/1e4`，net = gross − cost）。

---

## 卡 W4-T05 — 信号 parity 测试（双信号链 2024 重叠区逐位比对）

- **目标（一句话）**: 证明 T03 新信号链（权威 loader）与 quick_verify 参考信号链（data_loader_patch）在 2024 切片上，凡不依赖 funding 源的序列（z / size_scale / z_slope）逐位一致，并量化 fund_allow 因 funding 双源产生的分歧率。
- **机器**: **mac**（`data_loader_patch.py` L16 硬编码 `/Users/mark/multica/quant-loop`，105 上不存在该路径）。预估 **15 min**。
- **读**（全部只读，存在性已核对）:
  - `quant-loop/research/swarm/2026-07-25/signal-enhance-h3/run_experiments.py`（import `enhance_signals`, `load_config`）
  - `quant-loop/research/swarm/2026-07-25/signal-enhance-h3/data_loader_patch.py`（import as `dlp`）
  - `FH/se_h3_common.py`（T02 产物；提供 `load_aligned_data()` → `(d1m, funding, common_idx)`）
  - `FH/se_h3_signals.py`（T03 产物；提供 `build_se_h3_signals(d1m, cfg, funding)`，slope 键名 `z_slope_fav_4`）
- **写**: `FH/test_signal_parity.py`（唯一新文件）；失败时诊断写 `FH/results/t05_signal_parity_failure.json`。
- **依赖**: T02、T03（wave 1）。开跑前先 `ls FH/se_h3_common.py FH/se_h3_signals.py`，缺任何一个 → STOP，报「上游产物缺失」，不要自己补写。

### 步骤

1. 写 `FH/test_signal_parity.py`，头部 sys.path 装配：

```python
import sys, json
from pathlib import Path
import numpy as np, pandas as pd

FH = Path(__file__).resolve().parent
SE = FH.parent                          # signal-enhance-h3/
FIXED = SE.parent / "H3-variants-h1h2h4"
for p in (str(FH), str(SE), str(FIXED)):
    if p not in sys.path:
        sys.path.insert(0, p)

import data_loader_patch as dlp
from run_experiments import enhance_signals, load_config
import se_h3_common as C
import se_h3_signals as S

START, END = "2024-01-01", "2024-12-31"
PAIR = "BTCUSDT/SOLUSDT"
cfg = load_config()   # H3 config.json，两条链共用同一份
```

2. 参考链（path B，quick_verify 原路）：

```python
d1m_b, fund_b = dlp.slice_by_date(dlp.load_all(), dlp.load_funding(), START, END)
sig_b = enhance_signals(d1m_b, cfg, fund_b)[PAIR]
```

3. 新链（path A，权威 loader + T03）。注意 `C.load_aligned_data()` 返回全历史，必须用与 `dlp.slice_by_date` 相同的闭区间 mask 裁 2024：

```python
d1m_a, fund_a, _ = C.load_aligned_data()
t0, t1 = pd.Timestamp(START), pd.Timestamp(END)
d1m_a = {s: df.loc[(df.index >= t0) & (df.index <= t1)].copy() for s, df in d1m_a.items()}
fund_a = {s: f.loc[(f.index >= t0) & (f.index <= t1)].copy() for s, f in fund_a.items()}
sig_a = S.build_se_h3_signals(d1m_a, cfg, fund_a)[PAIR]
```

4. 索引交集断言 + 逐位比对（funding 无关序列必须全等）：

```python
idx = sig_a["z"].index.intersection(sig_b["z"].index)
assert len(sig_b["z"].index) == 525601, f"ref bars {len(sig_b['z'].index)} != 525601"
assert len(idx) == 525601, f"index overlap {len(idx)} != 525601"

for ka, kb in [("z", "z"), ("size_scale", "size_scale"), ("z_slope_fav_4", "z_slope_4")]:
    xa = sig_a[ka].reindex(idx).to_numpy(dtype=float)
    xb = sig_b[kb].reindex(idx).to_numpy(dtype=float)
    ok = np.allclose(xa, xb, atol=1e-12, rtol=0.0, equal_nan=True)
    print(f"{ka} vs {kb}: allclose(1e-12) = {ok}")
    assert ok, f"series mismatch: {ka} vs {kb}"
```

5. fund_allow 分歧量化（**不 assert 为 0**——funding 双源是已知事实；阈值 5%）：

```python
fa = sig_a["fund_allow"].reindex(idx).to_numpy(dtype=int)
fb = sig_b["fund_allow"].reindex(idx).to_numpy(dtype=int)
mism = int((fa != fb).sum()); pct = mism / len(idx) * 100.0
print(f"fund_allow mismatch: {mism} bars ({pct:.3f}%)")
assert pct <= 5.0, f"fund_allow divergence {pct:.2f}% > 5%"
print("SIGNAL PARITY OK")
```

6. 任一 assert 失败时，把 `{check, n_bars, first_mismatch_ts, mismatch_count}` 写进 `FH/results/t05_signal_parity_failure.json` 再 `sys.exit(1)`，便于主线诊断。

### 判定阈值与失败处理

| 检查 | 阈值 | 失败含义 → 处理 |
|---|---|---|
| 参考链 bar 数 | == 525601 | dlp 数据/切片漂移 → STOP，报数据层问题，不属本 harness bug |
| z / size_scale / z_slope | allclose atol=1e-12, rtol=0, equal_nan | T03 信号构造偏离 base 原语（z_slope 键名错、聚合规则错）→ 打回 T03，wave 2 不得放行 |
| fund_allow mismatch | ≤ 5% | >5% 说明权威 funding 与旧源差异过大，2024 的 8.07 锚点参考价值进一步下降 → 仍 STOP 并报主线（如实记录，不是代码 bug） |

### 验收

```bash
cd /Users/mark/multica/quant-loop/research/swarm/2026-07-25/signal-enhance-h3/full_history && \
/Users/mark/sdk/mamba-envs/trading/bin/python3 test_signal_parity.py; echo "exit=$?"
```

期望： stdout 含 3 行 `allclose(1e-12) = True`、1 行 `fund_allow mismatch: N bars (P%)`、末行 `SIGNAL PARITY OK`，`exit=0`，运行 < 2 min。

---

## 卡 W4-T06 — 循环 parity 双锚定（== base 引擎 且 == quick_verify 704/8.0735）

- **目标（一句话）**: 证明 T04 新回测循环 `backtest_pair_se` 在过滤器全关时与 base `_backtest_pair` 逐笔全等（锚点 a），过滤器全开且喂 quick_verify 自己的数据/信号时逐笔复现 704 trades、Sharpe 8.0735（锚点 b）。
- **机器**: **mac**（锚点 b 依赖 `data_loader_patch`，硬编码本机路径）。预估 **20 min**。
- **读**（全部只读）:
  - `FH/se_h3_common.py`（T02）、`FH/se_h3_signals.py`（T03）、`FH/se_h3_loop.py`（T04，签名须符合上文「跨卡接口契约」）
  - `quant-loop/strategies/_indicators/mtf_xs_pairs_base_20260718.py`（import `_backtest_pair`, `sharpe_daily_resampled`）
  - `signal-enhance-h3/run_experiments.py`（import `enhance_signals`, `backtest_variant`）
  - `signal-enhance-h3/data_loader_patch.py`
  - `signal-enhance-h3/quick_verify_2024.json`（锚点常量，不重新读也可，值已内联：704 / 8.0735）
- **写**: `FH/test_loop_parity.py`（唯一新文件）；失败时诊断写 `FH/results/t06_loop_parity_failure.json`。
- **依赖**: T02、T03、T04。**且建议 T05 先绿**（若 T05 的 z/z_slope 已证全等，T06 失败可干净地归因到循环而非信号）。开跑前 `ls FH/se_h3_common.py FH/se_h3_signals.py FH/se_h3_loop.py`，缺 → STOP。

### 步骤

1. sys.path 装配同 T05（FH、SE、FIXED 三个目录），import：

```python
import data_loader_patch as dlp
from run_experiments import enhance_signals, backtest_variant, load_config
from mtf_xs_pairs_base_20260718 import _backtest_pair, sharpe_daily_resampled
import se_h3_common as C
import se_h3_signals as S
from se_h3_loop import backtest_pair_se
```

注意 base 模块不在 FH/SE/FIXED 里——se_h3_common 已把 `strategies/` 和 `strategies/_indicators/` 装进 sys.path（T02 契约）；若 import 失败，显式 `sys.path.insert(0, "/Users/mark/multica/quant-loop/strategies/_indicators")`。

2. **锚点 (a)：过滤器全关 == base 引擎**。用权威 loader + T03 信号（2024 切片，切法同 T05 步骤 3）：

```python
cfg = load_config()
d1m_a, fund_a, _ = C.load_aligned_data()
# ...闭区间 mask 裁 2024（同 T05 步骤 3）...
sig = S.build_se_h3_signals(d1m_a, cfg, fund_a)[PAIR]

ref = _backtest_pair(sig, PAIR, sizing_scale=sig["size_scale"], fee_bps=1.0, slip_bps=1.0)
new = backtest_pair_se(sig, PAIR, sizing_scale=sig["size_scale"], fee_bps=1.0, slip_bps=1.0,
                       slope=None, adverse_stop_z=None, regime_break=3.0)
```

逐笔比对（base trade 的 ts 是 ISO 字符串，统一 `pd.Timestamp(...)` 再比）：

```python
rt, nt = ref["trades"], new["trades"]
assert len(rt) == len(nt) and len(rt) > 0, f"n_trades {len(nt)} vs ref {len(rt)}"
for k, (r, n) in enumerate(zip(rt, nt)):
    assert pd.Timestamp(r["entry_ts"]) == pd.Timestamp(n["entry_ts"]), f"trade {k} entry_ts"
    assert pd.Timestamp(r["exit_ts"]) == pd.Timestamp(n["exit_ts"]), f"trade {k} exit_ts"
    assert r["direction"] == n["direction"] and r["exit_reason"] == n["exit_reason"], f"trade {k} meta"
    assert abs(r["pnl_pct"] - n["pnl_pct"]) <= 1e-15, f"trade {k} pnl {r['pnl_pct']} vs {n['pnl_pct']}"
assert np.allclose(ref["bar_return"], new["bar_return"], atol=1e-15, rtol=0.0)
print(f"LOOP PARITY (a) OK vs base engine ({len(rt)} trades)")
```

3. **锚点 (b)：过滤器全开 == quick_verify 2024**。参考值用 quick_verify 自己的代码路径**现场重生成**（不信旧 json，json 只做独立交叉核对）：

```python
d1m_b, fund_b = dlp.slice_by_date(dlp.load_all(), dlp.load_funding(), "2024-01-01", "2024-12-31")
sigs_b = enhance_signals(d1m_b, cfg, fund_b)
sig_b = sigs_b[PAIR]
PARAMS = {"slope_filter": {"lookback": 4, "sign": "favorable"}, "adverse_stop_z": 0.7, "regime_break": 9.0}  # quick_verify.py L42

ref_b = backtest_variant(sigs_b, cfg, PARAMS)
assert len(ref_b["trades"]) == 704, f"reference regenerate: {len(ref_b['trades'])} != 704 — quick_verify 路径本身漂移，STOP"

new_b = backtest_pair_se(sig_b, PAIR, sizing_scale=sig_b["size_scale"], fee_bps=1.0, slip_bps=1.0,
                         slope=sig_b["z_slope_4"], adverse_stop_z=0.7, regime_break=9.0)
```

逐笔比对（参考侧净盈亏键名是 `net_pct`，新侧是 `pnl_pct`；毛盈亏两侧都是 `gross_pct`）：

```python
rt, nt = ref_b["trades"], new_b["trades"]
assert len(nt) == 704, f"new loop n_trades {len(nt)} != 704"
for k, (r, n) in enumerate(zip(rt, nt)):
    assert pd.Timestamp(r["entry_ts"]) == pd.Timestamp(n["entry_ts"]), f"trade {k} entry_ts"
    assert pd.Timestamp(r["exit_ts"]) == pd.Timestamp(n["exit_ts"]), f"trade {k} exit_ts"
    assert r["exit_reason"] == n["exit_reason"], f"trade {k} exit_reason {r['exit_reason']} vs {n['exit_reason']}"
    assert abs(r["net_pct"] - n["pnl_pct"]) <= 1e-12, f"trade {k} net pnl"
    assert abs(r["gross_pct"] - n["gross_pct"]) <= 1e-12, f"trade {k} gross pnl"
```

Sharpe 锚点（daily-resampled，base L760；index 用 `sig_b["a"].index`）：

```python
sr = sharpe_daily_resampled(new_b["bar_return"], sig_b["a"].index)["sharpe_daily_resampled"]
print(f"new-loop daily Sharpe = {sr:.4f} (anchor 8.0735)")
assert abs(sr - 8.0735) <= 1e-3, f"Sharpe {sr} off anchor 8.0735"
print("LOOP PARITY (b) OK vs quick_verify (704 trades, sharpe 8.0735)")
```

4. 失败诊断：任一 assert 失败时，把 `{anchor: "a"|"b", check, trade_index, ref_value, new_value, first_5_mismatches}` 写 `FH/results/t06_loop_parity_failure.json`，再 `sys.exit(1)`。

### 判定阈值与失败处理（质量闸门语义）

| 检查 | 阈值 | 失败归因 → 处理 |
|---|---|---|
| (a) n_trades / 逐笔 entry/exit/direction/exit_reason | 全等 | T04 复制 `_backtest_pair` 时改动了基础语义 → 打回 T04 |
| (a) pnl_pct / bar_return | atol=1e-15, rtol=0 | 成本口径或 sizing_scale 应用位置错 → 打回 T04 |
| (b) 参考侧重生成 n_trades | == 704 | quick_verify 参考路径自身漂移（数据被改）→ STOP，报数据层；**不是 T04 的锅** |
| (b) 新循环 n_trades | == 704 | (a) 绿而此处挂 → slope/adverse_stop 钩子逻辑错（符号约定、NaN 处理、exit 顺序）→ 打回 T04 |
| (b) 逐笔 pnl | atol=1e-12, rtol=0 | 同上 |
| (b) daily Sharpe | 8.0735 ± 1e-3 | 逐笔全绿但 Sharpe 挂几乎不可能；若发生，查 bar_return 的 size_scale 应用 |

**闸门规则**: (a)(b) 全绿 = PASS，wave 3（T07/T08）放行；任一红 = FAIL，T07-T15 全部阻塞，失败 json + 归因一并报主线。不允许「差不多绿」。

### 验收

```bash
cd /Users/mark/multica/quant-loop/research/swarm/2026-07-25/signal-enhance-h3/full_history && \
/Users/mark/sdk/mamba-envs/trading/bin/python3 test_loop_parity.py; echo "exit=$?"
```

期望：stdout 含 `LOOP PARITY (a) OK vs base engine` 与 `LOOP PARITY (b) OK vs quick_verify (704 trades, sharpe 8.0735)`，`exit=0`。运行目标 < 3 min（2024 切片 3 条 525k-bar Python 循环；> 10 min 视为异常，中止并报性能问题）。

---

## 跨 slice 冲突 / 协调事项

1. **T04 接口契约**: 本 slice 的 T06 钉死了 `backtest_pair_se` 签名与 trade 键集合（见「跨卡接口契约」）。负责细化 T02-T04 的 slice（w4-s2）必须让 `se_h3_loop.py` 满足该契约，否则 T06 必挂。这是两 slice 间唯一的硬耦合点。
2. **mac-only**: T05、T06 都只能在 mac 跑（`data_loader_patch.py` L16 硬编码 `/Users/mark/multica/quant-loop`；且 1m parquet 数据在 mac）。不能调度到 105。
3. **冻结只读依赖**: parity 锚定依赖以下文件字节级稳定——`strategies/_indicators/mtf_xs_pairs_base_20260718.py`、`strategies/mtf_xs_pairs_1m_15m_2h_h3_20260718/config.json`、`H3-variants-h1h2h4/run_btcsol_variants_fixed.py`、`signal-enhance-h3/{run_experiments,data_loader_patch,quick_verify}.py`、`data/perp_1m/*.parquet`、`data/funding/*.parquet`、`funding_analysis/BTCUSDT_funding.parquet`、graveyard 的 `SOLUSDT__funding.parquet`。任何清理/重构/数据统一类 workstream 在 sprint 期间动这些文件会让 T05/T06 假阴性失败。
4. **CPU**: 两卡各 2-3 min 单核，与 wave 1（T01-T04）无资源冲突；但不要与 wave 3 的长回测并发跑（耗时也是证据）。
