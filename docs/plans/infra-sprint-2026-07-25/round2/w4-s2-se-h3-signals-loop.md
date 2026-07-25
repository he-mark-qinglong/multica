# w4-s2 — se_h3_signals.py + se_h3_loop.py 执行卡片（round-2 细化）

- slice slug: `w4-s2-se-h3-signals-loop`
- 覆盖: W4 的 T03（信号模块）、T04（回测循环模块），来源 round1 `w4-signal-enhance-h3.md` §2。
- 日期: 2026-07-25
- 所有行号均已按当前工作区实读核实（2026-07-25）。

## 通用上下文（两张卡片共用，执行前必读）

**路径常量**

- `FH` = `/Users/mark/multica/quant-loop/research/swarm/2026-07-25/signal-enhance-h3/full_history/`
  （可能尚不存在，由卡片任务自行 `mkdir -p`；只许在该目录下新建文件，不得改任何既有文件）
- `QL` = `/Users/mark/multica/quant-loop`（quant-loop 根）
- base 引擎（只读）: `QL/strategies/_indicators/mtf_xs_pairs_base_20260718.py`（854 行）
- 2024 增强实验（只读参考）: `QL/research/swarm/2026-07-25/signal-enhance-h3/run_experiments.py`（406 行）
- H3 config（只读）: `QL/strategies/mtf_xs_pairs_1m_15m_2h_h3_20260718/config.json`
- python 一律 `/Users/mark/sdk/mamba-envs/trading/bin/python3`（默认 python3 缺 pyarrow）

**⚠️ 核心陷阱：adverse vs favorable slope 约定相反（两张卡片的灵魂）**

base 引擎 `_backtest_pair`（L463-605）内置的 H1 slope 检查是 **adverse 约定**：

- base L475: `slope = signals.get("z_slope_15m")`
- base L503-507（原文）:
  ```python
  if "z_slope_15m" in p or slope is not None:  # H1 — z-slope confirm
      if direction == +1 and (sl is None or sl >= 0):
          allow = False
      if direction == -1 and (sl is None or sl <= 0):
          allow = False
  ```
  语义：direction=+1（z ≤ −z_entry，z 处于负极值）要求 **slope < 0**（z 仍在恶化、
  「冲进极值」时才入场）；slope ≥ 0 或 NaN 一律拒入。direction=−1 镜像要求 slope > 0。

signal-enhance 要的是 **favorable 约定**（run_experiments.py L182-190，原文）：

  ```python
  if sign == "favorable":
      # long_a_short_b expects z rising from a negative extreme
      if direction == +1 and (sl is None or sl <= 0):
          allow = False
      if direction == -1 and (sl is None or sl >= 0):
          allow = False
  ```
  语义：direction=+1 要求 **slope > 0**（z 已从负极值拐头回升、均值回归已启动才入场）；
  slope ≤ 0 或 NaN 一律拒入。direction=−1 镜像要求 slope < 0。

**转换逻辑（必须严格照此实现，逐条对照）**

| 情形 | base adverse（H1，L503-507） | se favorable（L185-190） |
|---|---|---|
| direction=+1 放行条件 | sl < 0（严格） | sl > 0（严格） |
| direction=+1 拒入条件 | sl ≥ 0 或 sl is None | sl ≤ 0 或 sl is None |
| direction=−1 放行条件 | sl > 0（严格） | sl < 0（严格） |
| direction=−1 拒入条件 | sl ≤ 0 或 sl is None | sl ≥ 0 或 sl is None |
| NaN 处理 | 拒入 | 拒入（相同） |

即：favorable 恰好是 adverse 在严格不等式上的**逻辑补集**。因此：

1. **绝不能**把新 slope 序列命名为 `z_slope_15m` 塞进 signals dict——base 循环会对它施加
   方向相反的 adverse 过滤。新键名一律 `z_slope_fav_4`。
2. 复制 `_backtest_pair` 时，L503-507 的 adverse 块保留原样（它对新 signals 是惰性的：
   `signals.get("z_slope_15m")` 返回 None 且 params 无 `z_slope_15m` 键，条件不触发），
   favorable 检查作为**独立新块**加在 fund_allow 检查（base L522-525）之后。
3. 边界（=0 拒入、NaN 拒入）两边一致，照抄 run_experiments 的 `<=`/`>=` 不要改成 `<`/`>`。

**exit 链顺序（T04 用）**：run_experiments L228-239 的实际顺序是
`z_mean_revert → regime_break → adverse_stop → max_holding`（注意 adverse_stop 在
regime_break **之后**、max_holding 之前）。锁定的 regime_break=9.0 下 regime_break 几乎
不触发（入场 |z|≥2.5，adverse stop 只要求反向走 0.7），但顺序影响 exit_reason 标注，
必须与 quick_verify 完全一致，否则下游 T06 parity（trades 数==704）会假阴性失败。

---

## T03 — 信号模块 `se_h3_signals.py`（+ 冒烟脚本）

- **目标一句话**: baseline H3 信号 + favorable 用 15m z-slope 列（键名 `z_slope_fav_4`），
  全部调用 base 模块原语，零复制指标逻辑。
- **机器**: mac（需要 data/ parquet）。**预估**: 20 min。**依赖**: 无（自带 sys.path 引导，不依赖 T02）。
- **读**（只读）:
  - `QL/strategies/_indicators/mtf_xs_pairs_base_20260718.py` — `build_h3_signals` L318-381、
    `aggregate_ohlcv` L39-60、`zscore_slope` L94-109、`align_lower_to_upper` L63-70
  - `QL/research/swarm/2026-07-25/signal-enhance-h3/run_experiments.py` L68-72（slope 构造样板）
  - `QL/strategies/mtf_xs_pairs_1m_15m_2h_h3_20260718/config.json`（indicators L15-24）
  - `QL/data/perp_1m/BTCUSDT_1m.parquet`、`QL/data/perp_1m/SOLUSDT_1m.parquet`（冒烟用）
- **写**（新建，仅此两个文件）:
  - `FH/se_h3_signals.py`
  - `FH/smoke_signals.py`

### 步骤

1. `mkdir -p FH`。
2. 写 `FH/se_h3_signals.py`，结构：

   ```python
   """se-h3 signals: baseline H3 signals + favorable 15m z-slope column.

   Read-only imports from the production base module; nothing here mutates
   shared code. Key naming: the slope column is `z_slope_fav_4`, NEVER
   `z_slope_15m` (that key triggers the base engine's ADVERSE H1 filter,
   which is the exact opposite convention — see round2 card w4-s2).
   """
   from __future__ import annotations

   import sys
   from pathlib import Path

   import pandas as pd

   # sys.path bootstrap (self-contained; do not rely on se_h3_common).
   QL_ROOT = Path(__file__).resolve().parents[5]  # quant-loop root
   _STRAT = QL_ROOT / "strategies"
   for _p in (str(_STRAT), str(_STRAT / "_indicators")):
       if _p not in sys.path:
           sys.path.insert(0, _p)

   from mtf_xs_pairs_base_20260718 import (  # noqa: E402
       aggregate_ohlcv,
       align_lower_to_upper,
       build_h3_signals,
       zscore_slope,
   )

   SLOPE_LOOKBACK = 4          # locked by pre-registered SPEC
   SLOPE_KEY = "z_slope_fav_4"  # never "z_slope_15m" (adverse-hook collision)

   __all__ = ["build_se_h3_signals", "SLOPE_LOOKBACK", "SLOPE_KEY"]


   def build_se_h3_signals(d1m: dict, cfg: dict, funding: dict) -> dict:
       """Baseline H3 signals (base L318-381) + favorable slope column.

       Mirrors run_experiments.enhance_signals L68-72 exactly:
       pair z (1m) -> aggregate to 15min -> zscore_slope(., 4) -> ffill to 1m.
       """
       sigs = build_h3_signals(d1m, cfg, funding)
       for pair in cfg["pairs"]:
           z = sigs[pair]["z"]
           z_15m = aggregate_ohlcv(z.rename("z").to_frame(), "15min")["z"]
           slope_15m = zscore_slope(z_15m, SLOPE_LOOKBACK).rename(SLOPE_KEY)
           # align onto the pair's own 1m index (== sigs[pair]["a"].index)
           sigs[pair][SLOPE_KEY] = align_lower_to_upper(sigs[pair]["a"], slope_15m)
       return sigs
   ```

   注意点：
   - 用 `sigs[pair]["a"]` 做 align 目标（它已被 `build_h3_signals` loc 到 common 索引，
     与 `z` 同索引）；不要重新从 `d1m` 算 common。
   - resample rule 用 `"15min"`（与 base L364、run_experiments L71 一致，勿写 `"15T"`）。
   - 不要加 `z_slope_8`、`spread_ret`、`fund_diff` 等 run_experiments 里的其他诊断列——
     本 SPEC 只用 lookback=4 一个组合（cycle-46 纪律，不做参数扫荡）。

3. 写 `FH/smoke_signals.py`（60 000 bar 冒烟，自带数据加载，不依赖 T02）：

   ```python
   """Smoke test for se_h3_signals on a 60k-bar slice. Run: python3 smoke_signals.py"""
   import json
   import sys
   import time
   from pathlib import Path

   import pandas as pd

   HERE = Path(__file__).resolve().parent
   sys.path.insert(0, str(HERE))
   QL_ROOT = HERE.parents[4]  # full_history -> signal-enhance-h3 -> 2026-07-25 -> swarm -> research -> quant-loop
   # NOTE: parents[4] from the FILE (HERE is already the file's dir): count carefully:
   # HERE=full_history, .parent=signal-enhance-h3, .parent=2026-07-25, .parent=swarm,
   # .parent=research, .parent=quant-loop  => HERE.parents[4]

   from se_h3_signals import build_se_h3_signals, SLOPE_KEY  # noqa: E402

   N_BARS = 60_000


   def load_1m(symbol: str) -> pd.DataFrame:
       # Mirrors fixed runner load_perp_1m (run_btcsol_variants_fixed.py L90-98).
       p = QL_ROOT / "data" / "perp_1m" / f"{symbol}_1m.parquet"
       df = pd.read_parquet(p)
       df["open_time"] = pd.to_datetime(df["open_time"].astype("int64"), unit="ms", utc=True)
       df = df.set_index("open_time").sort_index()
       df.index = df.index.tz_convert(None)
       keep = [c for c in ("open", "high", "low", "close", "volume") if c in df.columns]
       return df[keep].astype(float)


   def main() -> None:
       t0 = time.time()
       d1m = {s: load_1m(s) for s in ("BTCUSDT", "SOLUSDT")}
       common = d1m["BTCUSDT"].index.intersection(d1m["SOLUSDT"].index)[:N_BARS]
       d1m = {s: df.loc[common].copy() for s, df in d1m.items()}
       cfg = json.loads((QL_ROOT / "strategies" / "mtf_xs_pairs_1m_15m_2h_h3_20260718"
                         / "config.json").read_text())
       # funding={} -> base _fund_2h defaults fund_allow=1 (base L347-348); fine for smoke.
       sigs = build_se_h3_signals(d1m, cfg, funding={})
       pair = cfg["pairs"][0]
       sig = sigs[pair]
       assert SLOPE_KEY in sig, f"missing {SLOPE_KEY}"
       slope = sig[SLOPE_KEY]
       assert slope.index.equals(sig["z"].index), "slope/z index mismatch"
       frac = float(slope.notna().mean())
       assert frac > 0.9, f"slope non-NaN fraction {frac:.3f} <= 0.9"
       assert "z_slope_15m" not in sig, "forbidden key z_slope_15m present"
       print(f"SMOKE OK: bars={len(common)} slope_nonNaN={frac:.4f} "
             f"elapsed={time.time()-t0:.1f}s")


   if __name__ == "__main__":
       main()
   ```

   实现时先实际数一遍 `parents` 层级并打印 `QL_ROOT` 验证（`assert (QL_ROOT / "data" / "perp_1m").is_dir()`），
  层级错了立刻在 assert 处暴露，不要静默。

4. 运行验收（下方）。预期 warmup ≈ 240(z lookback)+少量 15m 聚合+slope 4 ≈ 几百 bar，
   60 000 bar 切片上 non-NaN 比例应 >0.99。

### 验收（机械）

```bash
cd /Users/mark/multica/quant-loop/research/swarm/2026-07-25/signal-enhance-h3/full_history
/Users/mark/sdk/mamba-envs/trading/bin/python3 smoke_signals.py
```
- 期望: 输出 `SMOKE OK: bars=60000 slope_nonNaN=...`，exit 0，运行 <2 min。
- 附加 invariant: `grep -c "z_slope_15m" se_h3_signals.py` 输出 `0`
  （注释里提到不算——用 `grep -c '"z_slope_15m"'` 且排除注释，或直接目检无该字符串作为键使用）；
  `grep -c "z_slope_fav_4" se_h3_signals.py` ≥ 2。

---

## T04 — 回测循环模块 `se_h3_loop.py`

- **目标一句话**: 逐行复制 base `_backtest_pair` 并加 favorable slope 入场钩子 + adverse_stop
  出场钩子，语义与 quick_verify（run_experiments.backtest_variant）完全一致；trade dict 兼容
  权威指标函数。
- **机器**: mac（验收含与 base `_backtest_pair` 的合成数据对照，不读 parquet，但仓库在 mac）。
  **预估**: 30 min。**依赖**: 无（自带 sys.path 引导；T06 才需要 T03 的产物）。
- **读**（只读）:
  - `QL/strategies/_indicators/mtf_xs_pairs_base_20260718.py` — `_backtest_pair` **L463-605**
    （复制源）、`Trade` dataclass L178-194、`_trade_dict` L197-202、`build_portfolio` L612-622、
    `run_backtest` H3 路径 L827-855（`run_se_h3` 的镜像样板）
  - `QL/research/swarm/2026-07-25/signal-enhance-h3/run_experiments.py` — 钩子语义样板：
    favorable 过滤 L182-190、exit 链 L228-239（adverse_stop L233-237）、成本 L157-159
  - `QL/research/swarm/2026-07-25/H3-variants-h1h2h4/run_btcsol_variants_fixed.py` —
    `portfolio_metrics` L220-239（消费 `t["pnl_pct"]`，见 L224）、`fee_shock_metrics` L313-346
    （消费 `t["exit_ts"]`，见 L320）——这两个函数决定 trade dict 必须含的键。
- **写**（新建，仅此一个文件）: `FH/se_h3_loop.py`

### 步骤

1. `mkdir -p FH`（若已存在跳过）。
2. 文件头 + sys.path 引导（同 T03 卡片第 2 步的 bootstrap，`parents[5]` 到 quant-loop 根），
   imports:

   ```python
   from mtf_xs_pairs_base_20260718 import (  # noqa: E402
       Trade,
       _backtest_pair,   # used by selftest() parity check only
       _trade_dict,
       build_portfolio,
   )
   from se_h3_signals import build_se_h3_signals  # noqa: E402  (sys.path: FH dir first)
   ```

   （`import numpy as np`、`import pandas as pd`、`from typing import Optional` 同 base。）
   文件顶部 docstring 写明：「Verbatim copy of base `_backtest_pair` (mtf_xs_pairs_base_20260718.py
   L463-605) with exactly three modifications (a)(b)(c) below. The base H1 adverse slope block is
   retained but INERT (our signals never carry `z_slope_15m`); the favorable filter is a separate
   new block with the OPPOSITE sign convention.」

3. `backtest_pair_se`：把 base L463-605 整个函数体复制过来，签名改为：

   ```python
   def backtest_pair_se(signals: dict, pair: str,
                        sizing_scale: Optional[pd.Series] = None,
                        fee_bps: float = 1.0, slip_bps: float = 1.0,
                        slope_sign: Optional[str] = "favorable",
                        adverse_stop_z: Optional[float] = 0.7,
                        regime_break: Optional[float] = None) -> dict:
   ```

   - `slope_sign=None` → 完全跳过 slope 过滤（T06 parity 锚 (a) 的「过滤全关」态）。
   - `adverse_stop_z=None` → 跳过逆势止损。
   - `regime_break=None` → 回落到 base 语义 `float(p.get("regime_break", 3.0))`（base L473）；
     调用方（run_se_h3）显式传 9.0。

   改动 (a) — favorable slope 入场钩子：在复制体的 fund_allow 块（对应 base L522-525）**之后**、
   H2 VPVR 块之前，插入（语义逐字镜像 run_experiments L182-190）：

   ```python
   slope_fav = signals.get("z_slope_fav_4")
   if slope_sign is not None and slope_fav is not None:
       sl_f = float(slope_fav.iat[i]) if np.isfinite(slope_fav.iat[i]) else None
       if slope_sign == "favorable":
           # enter only after z has turned back toward the mean
           if direction == +1 and (sl_f is None or sl_f <= 0):
               allow = False
           if direction == -1 and (sl_f is None or sl_f >= 0):
               allow = False
       else:
           raise ValueError(f"unsupported slope_sign: {slope_sign!r}")
   ```

   注意：`sl_f` 必须在该 bar 的循环内、entry 判定前取（与 base 取 `sl` 的 L491 同位置或
   过滤块内现取均可，但每个 i 只取一次、用 `.iat[i]`，勿用标签索引）。

   改动 (b) — adverse_stop 出场钩子：exit 链（复制体中对应 base L562-568）改为
   `z_mean_revert → regime_break → adverse_stop → max_holding`，严格镜像
   run_experiments L228-239 的 if 链结构（不用 elif、每级判 `exit_reason is None`）：

   ```python
   exit_reason = None
   if abs(zi) <= z_exit:
       exit_reason = "z_mean_revert"
   if exit_reason is None and ((pos == +1 and zi <= -regime_break) or
                               (pos == -1 and zi >= +regime_break)):
       exit_reason = "regime_break"
   if exit_reason is None and adverse_stop_z is not None:
       if pos == +1 and zi <= entry_z - adverse_stop_z:
           exit_reason = "adverse_stop"
       if pos == -1 and zi >= entry_z + adverse_stop_z:
           exit_reason = "adverse_stop"
   if exit_reason is None and bars_held >= max_hold:
       exit_reason = "max_holding"
   ```

   其中 `regime_break` 在函数开头解析：
   `rb = float(regime_break) if regime_break is not None else float(p.get("regime_break", 3.0))`。
   ⚠️ 注意复制体里原 base L473 的 `regime_break` 局部变量要替换成这个 rb 逻辑，别留两个来源。

   改动 (c) — trade dict 键：保留 base 的 `Trade(...)` 构造（L578-594 原样，`pnl_pct=net`），
   `_trade_dict(t)` 之后追加 gross：

   ```python
   d = _trade_dict(t)
   d["gross_pct"] = pct   # pre-cost pair return; pnl_pct stays NET (base L576-577)
   trade_dicts.append(d)
   ```

   返回 dict 结构与 base L598-605 相同（`pair/trades/bar_return/n_bars/span_start/span_end`）。
   兼容性断言（写进模块 docstring）：`pnl_pct`(net) 供 `portfolio_metrics`（fixed runner L224），
   `exit_ts`（`_trade_dict` 已 isoformat 化，L200-201）供 `fee_shock_metrics`（L320
   `pd.to_datetime(t["exit_ts"])` 可解析字符串），`exit_reason` 供归因统计，`gross_pct` 供
   成本敏感性复核。

4. `run_se_h3(d1m, cfg, funding)`：镜像 base `run_backtest` 的 H3 路径（L809-855）：

   ```python
   SE_H3_DEFAULTS = {"slope_sign": "favorable", "adverse_stop_z": 0.7, "regime_break": 9.0}

   def run_se_h3(d1m: dict, cfg: dict, funding: Optional[dict] = None) -> dict:
       fee_bps = float(cfg.get("fees_bps_per_side", 1.0))
       slip_bps = float(cfg.get("slippage_bps_per_side", 1.0))
       se = {**SE_H3_DEFAULTS, **cfg.get("se_h3", {})}
       # tz-normalise d1m and funding exactly like base L816-836, then:
       signals_by_pair = build_se_h3_signals(d1m_norm, cfg, f_norm)
       per_pair = []
       for pair, sig in signals_by_pair.items():
           per_pair.append(backtest_pair_se(
               sig, pair, sizing_scale=sig.get("size_scale"),
               fee_bps=fee_bps, slip_bps=slip_bps,
               slope_sign=se["slope_sign"], adverse_stop_z=se["adverse_stop_z"],
               regime_break=se["regime_break"],
           ))
       starting_cap = float(cfg.get("starting_capital_usd", 100000.0))
       portfolio = build_portfolio(per_pair, starting_capital=starting_cap)  # base L612
       return {"per_pair": per_pair, "portfolio": portfolio}
   ```

   （tz-normalise 段直接复制 base L816-836 的两个循环，勿省略——funding 索引带 tz 时
   `build_h3_signals` 内部 L351-352 还会再兜一次，但保持与 base 逐位一致。）

5. `__all__ = ["backtest_pair_se", "run_se_h3", "SE_H3_DEFAULTS"]`。

6. 模块内置 `selftest()`（不进 `__all__` 也可，供验收调用）——全合成数据，不读 parquet：

   ```python
   def selftest() -> None:
       import numpy as np, pandas as pd
       n = 600
       idx = pd.date_range("2024-01-01", periods=n, freq="1min")
       rng = np.random.default_rng(0)
       a = pd.DataFrame({"close": 100 * np.exp(np.cumsum(rng.normal(0, 1e-4, n)))},
                        index=idx)
       b = pd.DataFrame({"close": 50 * np.exp(np.cumsum(rng.normal(0, 1e-4, n)))},
                         index=idx)
       z = pd.Series(np.sin(np.arange(n) / 25.0) * 4.0, index=idx, name="z")  # sweeps ±4
       slope_pos = pd.Series(1.0, index=idx, name="z_slope_fav_4")   # always favorable
       slope_neg = pd.Series(-1.0, index=idx, name="z_slope_fav_4")
       params = {"z_entry": 2.5, "z_exit": 0.5, "max_hold": 240}

       def mk(sl):
           return {"a": a, "b": b, "z": z, "fund_allow": pd.Series(1, index=idx),
                   "z_slope_fav_4": sl, "params": params}

       # 1) favorable + positive slope -> trades exist
       r1 = backtest_pair_se(mk(slope_pos), "A/B")
       assert len(r1["trades"]) > 0, "favorable/pos-slope produced no trades"
       # 2) favorable + negative slope -> direction=+1 entries all rejected.
       #    With a symmetric z sweep every short (dir=-1) is also rejected when
       #    slope sign disagrees; a constant -1 slope admits only dir=-1 entries.
       r2 = backtest_pair_se(mk(slope_neg), "A/B")
       assert all(t["direction"] == "short_a_long_b" for t in r2["trades"]), \
           "favorable filter leaked a long entry under negative slope"
       # 3) filters OFF == base engine, trade-by-trade
       sig_off = {"a": a, "b": b, "z": z, "fund_allow": pd.Series(1, index=idx),
                  "params": params}
       r_off = backtest_pair_se(sig_off, "A/B", slope_sign=None,
                                 adverse_stop_z=None, regime_break=3.0)
       r_base = _backtest_pair(sig_off, "A/B")
       assert len(r_off["trades"]) == len(r_base["trades"]), "filter-off trade count != base"
       for t_new, t_ref in zip(r_off["trades"], r_base["trades"]):
           assert t_new["entry_ts"] == t_ref["entry_ts"]
           assert t_new["exit_ts"] == t_ref["exit_ts"]
           assert t_new["exit_reason"] == t_ref["exit_reason"]
           assert abs(t_new["pnl_pct"] - t_ref["pnl_pct"]) < 1e-15
           assert "gross_pct" in t_new and "exit_ts" in t_new and "exit_reason" in t_new
       # 4) adverse_stop fires with regime_break=9.0 (locked value)
       r_stop = backtest_pair_se(sig_off, "A/B", slope_sign=None,
                                  adverse_stop_z=0.7, regime_break=9.0)
       reasons = {t["exit_reason"] for t in r_stop["trades"]}
       assert "adverse_stop" in reasons, f"adverse_stop never fired: {reasons}"
       print("SELFTEST OK: favorable filter, filter-off parity with base, adverse_stop all verified")
   ```

   说明：selftest 第 3 条就是下游 T06 parity 锚 (a) 的迷你版——过滤器全关时必须与 base
   `_backtest_pair` 逐笔一致（这正是「逐行复制、只改三处」的直接证明）。若第 4 条因合成
   z 形状不触发 adverse_stop，可调 `np.sin` 的频率/幅度（z 是手工造的，允许调），
   但不得放宽断言。

### 验收（机械）

```bash
cd /Users/mark/multica/quant-loop/research/swarm/2026-07-25/signal-enhance-h3/full_history
/Users/mark/sdk/mamba-envs/trading/bin/python3 -c "import se_h3_loop; assert {'backtest_pair_se','run_se_h3'} <= set(se_h3_loop.__all__); se_h3_loop.selftest()"
```
- 期望: 打印 `SELFTEST OK: ...`，exit 0，运行 <30 s（600 bar 合成循环）。
- 附加 invariant: `grep -c "adverse_stop" se_h3_loop.py` ≥ 2；
  `grep -c "z_slope_fav_4" se_h3_loop.py` ≥ 1。

---

## 片内依赖与冲突备注

- T03、T04 互不依赖（文件不相交：`se_h3_signals.py`/`smoke_signals.py` vs `se_h3_loop.py`），可并行；
  但 T04 的 `run_se_h3` import `se_h3_signals`——selftest 不触发该 import 路径之外的调用，
  若 T03 尚未完成，T04 验收会因 `from se_h3_signals import ...` 失败。**调度建议：T03 先行或
  两者同波但 T04 验收排在 T03 之后**；若必须严格并行，T04 可把该 import 移入 `run_se_h3`
  函数体内（延迟 import），卡片作者可自选，验收不变。
- 下游（非本 slice）: T05 依赖 T03，T06 依赖 T03+T04。本两张卡全绿是 T06 质量闸门的前提。
- 与 T02（se_h3_common.py，别的 slice）的良性重叠：T02/T03/T04 各自写一份 sys.path
  bootstrap，是有意的零依赖设计，后续聚合阶段可收敛，当前不改。
- 跨 workstream 冻结要求：本 slice 的语义锚定依赖以下文件字节级稳定——
  `strategies/_indicators/mtf_xs_pairs_base_20260718.py`、
  `signal-enhance-h3/run_experiments.py`、
  `H3-variants-h1h2h4/run_btcsol_variants_fixed.py`、H3 `config.json`。
  任何清理/重构类 workstream 若动这些文件，本 slice 的 parity 断言会假阴性失败（继承 round1 §5.1）。
