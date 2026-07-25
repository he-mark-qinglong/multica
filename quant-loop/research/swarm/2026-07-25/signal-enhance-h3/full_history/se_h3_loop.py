"""se-h3 loop: backtest loop module for the pre-registered signal-enhance-h3 variant.

Verbatim copy of base `_backtest_pair` (mtf_xs_pairs_base_20260718.py L463-605)
with exactly three modifications (a)(b)(c) below. The base H1 adverse slope
block is retained but INERT (our signals never carry `z_slope_15m`); the
favorable filter is a separate new block with the OPPOSITE sign convention.

Locked parameters (pre-registered SPEC, see full_history/SPEC_signal_enhance_h3_fullhist.md):
  slope_sign      = "favorable"
  adverse_stop_z  = 0.7
  regime_break    = 9.0   (overrides config.json default 3.0)

Module contract (this file ships ONLY this contract — no research findings):
  - backtest_pair_se(signals, pair, ..., slope_sign, adverse_stop_z, regime_break) -> dict
      Per-pair bar-by-bar backtest. Same return shape as base `_backtest_pair`.
      Trade dict keys: pnl_pct (NET, base-compatible), gross_pct (NEW, pre-cost),
      exit_ts (isoformat string, base-compatible), exit_reason, plus all base fields.
  - run_se_h3(d1m, cfg, funding=None) -> dict
      Mirrors base run_backtest's H3 path (L809-855): tz-normalise, build
      SE-H3 signals, run per-pair, build portfolio.
  - SE_H3_DEFAULTS = {"slope_sign": "favorable", "adverse_stop_z": 0.7, "regime_break": 9.0}
  - selftest() -> None
      All-synthetic-data parity check (no parquet reads).

Compatibility asserts (satisfied by construction below):
  - t["pnl_pct"]         net — consumed by portfolio_metrics (run_btcsol_variants_fixed L224)
  - t["exit_ts"]         isoformat str — consumed by fee_shock_metrics (L320, pd.to_datetime)
  - t["exit_reason"]     attribution field for downstream KEEP/KILL analysis
  - t["gross_pct"]       pre-cost pair return, available for cost-sensitivity re-runs
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

# sys.path bootstrap (self-contained; do not rely on se_h3_common).
# Layout: HERE=full_history; .parents[0]=signal-enhance-h3; [1]=2026-07-25;
# [2]=swarm; [3]=research; [4]=quant-loop; [5]=quant-loop root. parents[5] = quant-loop root.
QL_ROOT = Path(__file__).resolve().parents[5]
_STRAT = QL_ROOT / "strategies"
for _p in (str(_STRAT), str(_STRAT / "_indicators"), str(Path(__file__).resolve().parent)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from mtf_xs_pairs_base_20260718 import (  # noqa: E402
    Trade,
    _backtest_pair,   # used by selftest() parity check only
    _trade_dict,
    build_portfolio,
)
from se_h3_signals import build_se_h3_signals  # noqa: E402  (sys.path: FH dir first)

SE_H3_DEFAULTS = {"slope_sign": "favorable", "adverse_stop_z": 0.7, "regime_break": 9.0}

__all__ = ["backtest_pair_se", "run_se_h3", "SE_H3_DEFAULTS"]


def backtest_pair_se(signals: dict, pair: str,
                     sizing_scale: Optional[pd.Series] = None,
                     fee_bps: float = 1.0, slip_bps: float = 1.0,
                     slope_sign: Optional[str] = "favorable",
                     adverse_stop_z: Optional[float] = 0.7,
                     regime_break: Optional[float] = None) -> dict:
    """Per-pair bar-by-bar backtest with favorable slope entry + adverse_stop exit.

    Verbatim copy of base `_backtest_pair` (mtf_xs_pairs_base_20260718.py L463-605)
    with three modifications:

      (a) New favorable slope entry hook — INSIDE the entry block, AFTER the
          fund_allow check (base L522-525) and BEFORE the H2 VPVR block. Uses
          key `z_slope_fav_4` (NEVER `z_slope_15m` — that key triggers the
          opposite ADVERSE H1 filter). Mirror of run_experiments.py L182-190.

      (b) Exit chain rewired to `z_mean_revert -> regime_break -> adverse_stop
          -> max_holding` (4 ifs, NOT elif), strict mirror of
          run_experiments.py L228-239. The `regime_break` value is resolved
          at function top from the explicit kwarg (default 9.0 via
          SE_H3_DEFAULTS) or, if None, falls back to the base params dict
          semantic (float(p.get("regime_break", 3.0))) so that
          `slope_sign=None, adverse_stop_z=None, regime_break=None` reproduces
          base behaviour exactly.

      (c) trade dict gets an extra `gross_pct` key (pre-cost pair return).
          `pnl_pct` stays NET (base L576-577). All other fields unchanged.

    slope_sign=None  -> skip slope entry filter entirely (T06 parity anchor (a)).
    adverse_stop_z=None -> skip adverse_stop exit entirely (T06 parity anchor (a)).
    """
    a = signals["a"]
    b = signals["b"]
    common = a.index
    n = len(common)
    p = signals["params"]
    z = signals["z"]
    z_entry = float(p["z_entry"])
    z_exit = float(p.get("z_exit", 0.5))
    # Modification (b): single source of regime_break truth (no leftover base L473).
    rb = float(regime_break) if regime_break is not None else float(p.get("regime_break", 3.0))
    max_hold = int(p["max_hold"])
    slope = signals.get("z_slope_15m")           # base adverse key — INERT for SE-H3 signals
    trend = signals.get("trend_2h")

    trade_log = []
    pos = 0
    bars_held = 0
    entry_idx = None
    entry_a = entry_b = None
    entry_z = None
    entry_slope = None
    entry_trend = None
    pnl_per_bar = np.zeros(n)
    # H4-only 15m direction filter (price vs 15m EMA on each leg)
    pe15 = signals.get("price_ema_15m")
    for i in range(1, n):
        zi = float(z.iat[i]) if np.isfinite(z.iat[i]) else None
        sl = float(slope.iat[i]) if slope is not None and np.isfinite(slope.iat[i]) else None
        tr = int(trend.iat[i]) if trend is not None and np.isfinite(trend.iat[i]) else 0

        if pos == 0 and zi is not None:
            direction = 0
            if zi <= -z_entry:
                direction = +1
            elif zi >= +z_entry:
                direction = -1

            # hypothesis-specific entry filters
            allow = True
            if "z_slope_15m" in p or slope is not None:  # H1 — z-slope confirm (adverse; INERT for SE-H3)
                if direction == +1 and (sl is None or sl >= 0):
                    allow = False
                if direction == -1 and (sl is None or sl <= 0):
                    allow = False
            if pe15 is not None:  # H4 — 15m close-vs-EMA direction filter
                ta = int(pe15["trend_a"].iat[i]) if np.isfinite(pe15["trend_a"].iat[i]) else 0
                tb = int(pe15["trend_b"].iat[i]) if np.isfinite(pe15["trend_b"].iat[i]) else 0
                # long_a_short_b: a in uptrend (+1), b in downtrend (-1)
                # short_a_long_b: a in downtrend (-1), b in uptrend (+1)
                if direction == +1 and not (ta >= 1 and tb <= -1):
                    allow = False
                if direction == -1 and not (ta <= -1 and tb >= 1):
                    allow = False
            if trend is not None:  # H1, H4 — 2h regime cap (no counter-trend)
                if direction == +1 and tr < 0:
                    allow = False
                if direction == -1 and tr > 0:
                    allow = False
            fund_allow = signals.get("fund_allow")
            if fund_allow is not None:
                if int(fund_allow.iat[i]) == 0:
                    allow = False
            # Modification (a): favorable slope entry hook — mirror of run_experiments.py L182-190.
            # Uses `z_slope_fav_4` (NOT `z_slope_15m`). Only active when slope_sign is not None.
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
            # H2 VPVR edge-touch confirmation (must touch VAH/VAL)
            prof_15m = signals.get("prof_15m")
            prof_2h = signals.get("prof_2h")
            atr_1m = signals.get("atr")
            if prof_15m is not None and prof_2h is not None and atr_1m is not None:
                touch_k = float(p.get("touch_atr_k", 0.7))
                atr_v = float(atr_1m.iat[i]) if np.isfinite(atr_1m.iat[i]) else 0.0
                cl = float(a["close"].iat[i])
                vah15 = float(prof_15m["vah"].iat[i]) if np.isfinite(prof_15m["vah"].iat[i]) else np.nan
                val15 = float(prof_15m["val"].iat[i]) if np.isfinite(prof_15m["val"].iat[i]) else np.nan
                vah2h = float(prof_2h["vah"].iat[i]) if np.isfinite(prof_2h["vah"].iat[i]) else np.nan
                val2h = float(prof_2h["val"].iat[i]) if np.isfinite(prof_2h["val"].iat[i]) else np.nan
                tol = max(touch_k * atr_v, 1e-9)
                touches_long = any(np.isfinite(x) and abs(cl - x) <= tol for x in (val15, val2h))
                touches_short = any(np.isfinite(x) and abs(cl - x) <= tol for x in (vah15, vah2h))
                if direction == +1 and not touches_long:
                    allow = False
                if direction == -1 and not touches_short:
                    allow = False

            if allow and direction != 0:
                pos = direction
                entry_idx = i
                entry_a = float(a["close"].iat[i])
                entry_b = float(b["close"].iat[i])
                entry_z = zi
                entry_slope = sl
                entry_trend = tr
                bars_held = 1
        elif pos != 0:
            bars_held += 1
            a_ret = float(a["close"].iat[i]) / float(a["close"].iat[i - 1]) - 1.0
            b_ret = float(b["close"].iat[i]) / float(b["close"].iat[i - 1]) - 1.0
            scale = float(sizing_scale.iat[i]) if sizing_scale is not None and np.isfinite(sizing_scale.iat[i]) else 1.0
            pnl_per_bar[i] = pos * (a_ret - b_ret) / 2.0 * scale

            # Modification (b): exit chain re-ordered to z_mean_revert -> regime_break
            # -> adverse_stop -> max_holding (4 ifs, NOT elif). Mirror of
            # run_experiments.py L228-239. rb is resolved at function top.
            exit_reason = None
            if abs(zi) <= z_exit:
                exit_reason = "z_mean_revert"
            if exit_reason is None and ((pos == +1 and zi <= -rb) or
                                        (pos == -1 and zi >= +rb)):
                exit_reason = "regime_break"
            if exit_reason is None and adverse_stop_z is not None:
                if pos == +1 and zi <= entry_z - adverse_stop_z:
                    exit_reason = "adverse_stop"
                if pos == -1 and zi >= entry_z + adverse_stop_z:
                    exit_reason = "adverse_stop"
            if exit_reason is None and bars_held >= max_hold:
                exit_reason = "max_holding"
            if exit_reason:
                exit_a = float(a["close"].iat[i])
                exit_b = float(b["close"].iat[i])
                if pos == +1:
                    pct = (exit_a / entry_a - 1.0) - (exit_b / entry_b - 1.0)
                else:
                    pct = -(exit_a / entry_a - 1.0) + (exit_b / entry_b - 1.0)
                cost = 2.0 * 2.0 * (fee_bps + slip_bps) / 10_000.0
                net = pct - cost
                trade_log.append(Trade(
                    pair=pair,
                    direction="long_a_short_b" if pos == +1 else "short_a_long_b",
                    entry_ts=a.index[entry_idx],
                    entry_price_a=entry_a,
                    entry_price_b=entry_b,
                    exit_ts=a.index[i],
                    exit_price_a=exit_a,
                    exit_price_b=exit_b,
                    pnl_pct=net,
                    bars_held=bars_held,
                    z_at_entry=entry_z,
                    z_at_exit=zi,
                    slope15m_at_entry=entry_slope,
                    trend2h_at_entry=entry_trend,
                    exit_reason=exit_reason,
                ))
                pos = 0
                bars_held = 0
                entry_idx = entry_a = entry_b = entry_z = entry_slope = entry_trend = None

    # Modification (c): stamp gross_pct (pre-cost pair return) onto each trade
    # dict. pnl_pct stays NET (base L576-577 semantics).
    def _gross_for(t):
        return float(t.pnl_pct) + cost

    return {
        "pair": pair,
        "trades": [
            {**_trade_dict(t), "gross_pct": _gross_for(t)}
            for t in trade_log
        ],
        "bar_return": pnl_per_bar,
        "n_bars": n,
        "span_start": a.index[0].date().isoformat() if n else None,
        "span_end": a.index[-1].date().isoformat() if n else None,
    }


def run_se_h3(d1m: dict, cfg: dict, funding: Optional[dict] = None) -> dict:
    """Run signal-enhance-h3 backtest on a 1m dataset + funding feed.

    Mirrors base `run_backtest` H3 path (mtf_xs_pairs_base_20260718.py L827-855):
      1. tz-normalise d1m indices (base L816-821)
      2. tz-normalise funding indices if provided (base L831-836)
      3. build SE-H3 signals (our se_h3_signals.build_se_h3_signals — base H3
         signals + favorable z_slope_fav_4 column)
      4. per-pair backtest_pair_se with SE_H3_DEFAULTS (overridable via cfg["se_h3"])
      5. build_portfolio (base L612-622, same as base run_backtest for non-H4 paths)
    """
    fee_bps = float(cfg.get("fees_bps_per_side", 1.0))
    slip_bps = float(cfg.get("slippage_bps_per_side", 1.0))
    se = {**SE_H3_DEFAULTS, **cfg.get("se_h3", {})}

    # tz-normalise d1m exactly like base run_backtest L816-821.
    d1m_norm = {}
    for sym, df in d1m.items():
        if isinstance(df.index, pd.DatetimeIndex) and df.index.tz is not None:
            df = df.copy()
            df.index = df.index.tz_convert(None)
        d1m_norm[sym] = df

    # tz-normalise funding exactly like base run_backtest L831-836.
    f_norm = {}
    funding = funding or {}
    for sym, f in funding.items():
        if isinstance(f.index, pd.DatetimeIndex) and f.index.tz is not None:
            f = f.copy()
            f.index = f.index.tz_convert(None)
        f_norm[sym] = f

    signals_by_pair = build_se_h3_signals(d1m_norm, cfg, f_norm)

    per_pair = []
    for pair, sig in signals_by_pair.items():
        per_pair.append(backtest_pair_se(
            sig, pair,
            sizing_scale=sig.get("size_scale"),
            fee_bps=fee_bps, slip_bps=slip_bps,
            slope_sign=se["slope_sign"],
            adverse_stop_z=se["adverse_stop_z"],
            regime_break=se["regime_break"],
        ))

    starting_cap = float(cfg.get("starting_capital_usd", 100000.0))
    portfolio = build_portfolio(per_pair, starting_capital=starting_cap)
    return {"per_pair": per_pair, "portfolio": portfolio}


def selftest() -> None:
    """All-synthetic parity check (no parquet reads).

    Covers (all required by card):
      1. favorable + positive slope -> trades exist
      2. favorable + negative slope -> only dir=-1 entries pass
      3. filters OFF (slope_sign=None, adverse_stop_z=None) == base `_backtest_pair`
         trade-by-trade (entry_ts / exit_ts / exit_reason / pnl_pct bit-identical,
         plus new gross_pct / exit_ts / exit_reason keys present)
      4. adverse_stop fires with regime_break=9.0 (locked)
    """
    import numpy as np
    import pandas as pd
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