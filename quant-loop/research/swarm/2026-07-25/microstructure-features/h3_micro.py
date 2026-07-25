"""H3 baseline + microstructure-filtered backtests.

Reuses ``build_h3_signals`` from the production base but swaps the
per-pair backtest loop for a custom one that adds a trade-flow entry
filter (no production code modified).
"""
from __future__ import annotations

from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from mtf_xs_pairs_base_20260718 import (  # noqa: E402
    Trade,
    _trade_dict,
    build_h3_signals,
    build_portfolio,
)


def _backtest_pair_micro(
    signals: dict,
    pair: str,
    micro_diff: pd.Series,
    threshold: float,
    sizing_scale: Optional[pd.Series] = None,
    fee_bps: float = 1.0,
    slip_bps: float = 1.0,
) -> dict:
    """H3 per-pair loop with a microstructure entry filter.

    ``micro_diff`` is a signed composite (e.g. BTC flow pressure z-score
    minus SOL flow pressure z-score). A long-A-short-B entry (+1) is
    allowed only when ``micro_diff >= threshold`` at the signal bar; a
    short-A-long-B entry (-1) only when ``micro_diff <= -threshold``.
    """
    a = signals["a"]
    b = signals["b"]
    common = a.index
    n = len(common)
    p = signals["params"]
    z = signals["z"]
    z_entry = float(p["z_entry"])
    z_exit = float(p.get("z_exit", 0.5))
    regime_break = float(p.get("regime_break", 3.0))
    max_hold = int(p["max_hold"])

    trade_log: List[Trade] = []
    pos = 0
    bars_held = 0
    entry_idx = None
    entry_a = entry_b = None
    entry_z = None
    bars_held_count = 0
    pnl_per_bar = np.zeros(n)

    # ensure micro_diff is aligned to common index
    micro_diff = micro_diff.reindex(common).fillna(0.0)

    for i in range(1, n):
        zi = float(z.iat[i]) if np.isfinite(z.iat[i]) else None
        flow = float(micro_diff.iat[i])

        if pos == 0 and zi is not None:
            direction = 0
            if zi <= -z_entry:
                direction = +1
            elif zi >= +z_entry:
                direction = -1

            allow = True
            # H3 funding filter
            fund_allow = signals.get("fund_allow")
            if fund_allow is not None:
                if int(fund_allow.iat[i]) == 0:
                    allow = False

            # microstructure entry gate
            if direction == +1 and flow < threshold:
                allow = False
            if direction == -1 and flow > -threshold:
                allow = False

            if allow and direction != 0:
                pos = direction
                entry_idx = i
                entry_a = float(a["close"].iat[i])
                entry_b = float(b["close"].iat[i])
                entry_z = zi
                bars_held_count = 1
        elif pos != 0:
            bars_held_count += 1
            a_ret = float(a["close"].iat[i]) / float(a["close"].iat[i - 1]) - 1.0
            b_ret = float(b["close"].iat[i]) / float(b["close"].iat[i - 1]) - 1.0
            scale = float(sizing_scale.iat[i]) if sizing_scale is not None and np.isfinite(sizing_scale.iat[i]) else 1.0
            pnl_per_bar[i] = pos * (a_ret - b_ret) / 2.0 * scale

            exit_reason = None
            if abs(zi) <= z_exit:
                exit_reason = "z_mean_revert"
            elif (pos == +1 and zi <= -regime_break) or (pos == -1 and zi >= +regime_break):
                exit_reason = "regime_break"
            elif bars_held_count >= max_hold:
                exit_reason = "max_holding"
            if exit_reason:
                exit_a = float(a["close"].iat[i])
                exit_b = float(b["close"].iat[i])
                if pos == +1:
                    pct = (exit_a / entry_a - 1.0) - (exit_b / entry_b - 1.0)
                else:
                    pct = -(exit_a / entry_a - 1.0) + (exit_b / entry_b - 1.0)
                # two legs, entry+exit per leg => 2*2*(fee+slip) bps
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
                    bars_held=bars_held_count,
                    z_at_entry=entry_z,
                    z_at_exit=zi,
                    slope15m_at_entry=None,
                    trend2h_at_entry=None,
                    exit_reason=exit_reason,
                ))
                pos = 0
                bars_held_count = 0
                entry_idx = entry_a = entry_b = entry_z = None

    return {
        "pair": pair,
        "trades": [_trade_dict(t) for t in trade_log],
        "bar_return": pnl_per_bar,
        "n_bars": n,
        "span_start": a.index[0].date().isoformat() if n else None,
        "span_end": a.index[-1].date().isoformat() if n else None,
    }


def run_h3_with_micro(
    d1m: Dict[str, pd.DataFrame],
    funding: Dict[str, pd.Series],
    micro_map: Dict[str, pd.DataFrame],
    cfg: dict,
    flow_col: str = "flow_pressure_z",
    threshold: float = 0.0,
) -> dict:
    """Run H3 baseline or microstructure-filtered backtest.

    ``threshold == 0`` with no micro signal is equivalent to the baseline
    (the micro filter is vacuously true for all directions). Set
    ``threshold > 0`` to require confirming flow pressure.
    """
    # H3 signal builder expects tz-naive indices (matches base contract)
    d1m_norm = {}
    for sym, df in d1m.items():
        df = df.copy()
        if isinstance(df.index, pd.DatetimeIndex) and df.index.tz is not None:
            df.index = df.index.tz_convert(None)
        d1m_norm[sym] = df
    f_norm = {}
    for sym, f in funding.items():
        f = f.copy()
        if isinstance(f.index, pd.DatetimeIndex) and f.index.tz is not None:
            f.index = f.index.tz_convert(None)
        f_norm[sym] = f

    signals_by_pair = build_h3_signals(d1m_norm, cfg, f_norm)

    fee_bps = float(cfg.get("fees_bps_per_side", 1.0))
    slip_bps = float(cfg.get("slippage_bps_per_side", 1.0))

    per_pair = []
    for pair, sig in signals_by_pair.items():
        a_sym, b_sym = pair.split("/")
        micro_a = micro_map[a_sym]
        micro_b = micro_map[b_sym]
        # align micro features to the signal index
        common = sig["a"].index
        ma = micro_a[flow_col].reindex(common).fillna(0.0)
        mb = micro_b[flow_col].reindex(common).fillna(0.0)
        micro_diff = ma - mb
        size_scale = sig.get("size_scale")
        res = _backtest_pair_micro(
            sig, pair, micro_diff, threshold,
            sizing_scale=size_scale, fee_bps=fee_bps, slip_bps=slip_bps,
        )
        per_pair.append(res)

    starting_cap = float(cfg.get("starting_capital_usd", 100_000.0))
    portfolio = build_portfolio(per_pair, starting_capital=starting_cap)
    return {"per_pair": per_pair, "portfolio": portfolio}


def run_h3_baseline(d1m, funding, cfg):
    """Convenience wrapper: baseline H3 (threshold=0)."""
    return run_h3_with_micro(d1m, funding, {}, cfg, flow_col="flow_pressure_z", threshold=0.0)
