"""Backtest simulator for market-making strategies.

Replays historical aggTrades tick-by-tick, generating quotes, detecting
fills, managing inventory, applying adverse-selection guards, and emitting
closed round-trip trades compatible with the existing ``run_backtest.py``
equity-walk engine and ``gates/enforce.py`` gate system.

Modes (``MakerSimConfig.mode``):
  - ``single_position`` (legacy default): one open position at a time; a
    fill stops quoting until TP/SL/time exit closes the round-trip.  The
    A-S reservation price always sees ``inventory_qty=0``.
  - ``continuous``: true continuous market making — after a fill the
    simulator keeps quoting both sides, inventory state persists across
    the whole run and feeds ``reservation_price`` (A-S skew active), and
    the position is only force-flattened when
    ``inventory.flatten_required`` triggers (hard cap / time / stop-loss).

Limitations:
  - No L2 order-book data; fill inference uses the aggTrades tape as a
    proxy (validated by the T10 markout_demo approach).
  - Single symbol per simulator instance.
  - Exit fills are assumed immediate (taker fee).
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field, replace
from typing import Any

import numpy as np
import pandas as pd

from _shared.run_backtest import Trade

from _shared.market_making.fair_value import (
    FairValue,
    MarketSnapshot,
    compute_fair_value,
)
from _shared.market_making.reservation_price import (
    reservation_price,
    rolling_sigma,
)
from _shared.market_making.inventory import (
    InventoryState,
    empty_inventory,
    flatten_required,
    update_inventory,
)
from _shared.market_making.optimal_spread import (
    OptimalSpreadParams,
    optimal_half_spread,
)
from _shared.market_making.adverse_selection import (
    AdverseSelectionParams,
    AdverseSelectionState,
    ASK_LIFTED,
    BID_HIT,
    belief_update,
    decay_penalty,
    empty_state,
    is_quoting_allowed,
    on_fill,
)
from _shared.market_making.quoting_engine import (
    Quote,
    QuotingParams,
    generate_quotes,
)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class MakerSimConfig:
    """All knobs for the maker simulator."""

    symbol: str = "BTCUSDT"

    # Fair value
    vwap_lookback: int = 20
    vpvr_bars: int = 200
    spread_estimate_ticks: int = 2  # assumed book spread (no L2 data)

    # Reservation price
    gamma: float = 0.1
    sigma_window_seconds: int = 60
    horizon_seconds: float = 300.0

    # Quoting
    base_spread_bp: float = 2.0
    size_usd: float = 1000.0
    tick_size: float = 0.01
    inventory_skew_factor: float = 1.0
    vol_spread_coeff: float = 0.5

    # Inventory
    max_inventory_usd: float = 5000.0

    # Adverse selection
    fill_penalty_bp: float = 1.0
    penalty_decay_per_second: float = 0.5
    sweep_threshold: int = 3
    sweep_cooldown_seconds: float = 5.0
    max_penalty_bp: float = 10.0
    expected_sweep_cost_bp: float = 1.74

    # Exit
    max_hold_seconds: float = 300.0
    tp_bp: float = 5.0
    sl_bp: float = 10.0

    # Cost model
    maker_fee_bp: float = 2.0   # per side
    taker_fee_bp: float = 5.0   # per side (exit)

    # Simulation mode
    mode: str = "single_position"  # 'single_position' (legacy) | 'continuous'

    # Spread model: 'heuristic' (quoting_engine dynamic) or
    # 'optimal' (A-S closed-form half-spread from optimal_spread.py)
    spread_mode: str = "heuristic"
    kappa: float = 1.5  # order arrival intensity for optimal spread

    # Audit: when True, record every generated quote into metrics["quotes"]
    record_quotes: bool = False

    # Data window
    start_ts: str = "2026-04-19"
    end_ts: str = "2026-04-22"

    # Sampling: process every N-th trade (1 = every trade)
    trade_step: int = 1

    # Bar resampling for VPVR/VWAP
    bar_freq: str = "1min"


# ---------------------------------------------------------------------------
# Internal trade record (richer than run_backtest.Trade)
# ---------------------------------------------------------------------------

@dataclass
class FillRecord:
    ts: pd.Timestamp
    side: str           # 'buy' | 'sell'
    price: float
    qty: float
    fee_bp: float
    is_maker: bool


@dataclass
class RoundTrip:
    entry_ts: pd.Timestamp
    exit_ts: pd.Timestamp
    direction: str      # 'long' | 'short'
    entry_price: float
    exit_price: float
    qty: float
    pnl_usd: float
    pnl_bp: float
    maker_fee_bp: float
    taker_fee_bp: float
    exit_reason: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _resample_bars(trades: pd.DataFrame, freq: str) -> pd.DataFrame:
    """Aggregate aggTrades into OHLCV bars."""
    df = trades.set_index("ts")
    ohlcv = df["price"].resample(freq).ohlc()
    ohlcv["volume"] = df["qty"].resample(freq).sum()
    ohlcv = ohlcv.dropna(subset=["close"])
    return ohlcv


def _infer_book(trade_price: float, trade_is_buyer_maker: bool,
                spread_ticks: int, tick_size: float) -> tuple[float, float]:
    """Estimate best bid/ask from a single aggTrade print.

    ``is_buyer_maker=True`` → taker sold → fill at bid.
    ``is_buyer_maker=False`` → taker bought → fill at ask.
    """
    offset = spread_ticks * tick_size
    if trade_is_buyer_maker:
        bid = trade_price
        ask = trade_price + offset
    else:
        ask = trade_price
        bid = trade_price - offset
    return bid, ask


def _max_inventory_qty(price: float, max_inventory_usd: float) -> float:
    """Convert USD notional cap to asset quantity."""
    if price <= 0:
        return 0.0
    return max_inventory_usd / price


def roundtrip_to_trade(rt: RoundTrip) -> Trade:
    """Convert internal RoundTrip to run_backtest.Trade."""
    return Trade(
        entry_ts=rt.entry_ts,
        exit_ts=rt.exit_ts,
        direction=rt.direction,  # type: ignore[arg-type]
        size_fraction=1.0,
    )


# ---------------------------------------------------------------------------
# Continuous-mode helpers
# ---------------------------------------------------------------------------

def _apply_signed_fill(
    inventory: InventoryState,
    signed_qty: float,
    price: float,
    ts: pd.Timestamp,
) -> InventoryState:
    """Apply a signed fill under average-cost accounting.

    ``update_inventory`` blends ``(old_notional + fill_notional) / new_net``
    which is correct for opens and same-direction adds, but wrong for
    reducing fills: a partial close must leave the remainder at the prior
    cost basis, and a flip must reset the remainder's basis to the fill
    price.  Both cases are handled here so realized-PnL accounting in the
    simulator stays cash-exact.
    """
    net = inventory.net_qty
    if net == 0.0 or (net > 0) == (signed_qty > 0):
        # open or same-direction add: VWAP blend is correct
        return update_inventory(inventory, signed_qty, price, ts)
    if abs(signed_qty) < abs(net) - 1e-15:
        # partial reduce: remainder keeps the prior cost basis
        inv = update_inventory(inventory, signed_qty, price, ts)
        return replace(inv, avg_price=inventory.avg_price)
    if abs(signed_qty) <= abs(net) + 1e-15:
        # exact close
        return update_inventory(inventory, signed_qty, price, ts)
    # flip: close leg then opening leg so the remainder basis = fill price
    inv = update_inventory(inventory, -net, price, ts)
    return update_inventory(inv, signed_qty + net, price, ts)


def _reducing_roundtrip(
    inventory_before: InventoryState,
    exit_price: float,
    ts: pd.Timestamp,
    reducing_qty: float,
    maker_fee_bp: float,
    exit_fee_bp: float,
    exit_reason: str,
) -> RoundTrip:
    """Build a RoundTrip for the inventory-reducing portion of a fill."""
    direction = "long" if inventory_before.net_qty > 0 else "short"
    entry_price = inventory_before.avg_price
    if direction == "long":
        pnl_usd = (exit_price - entry_price) * reducing_qty
    else:
        pnl_usd = (entry_price - exit_price) * reducing_qty
    return RoundTrip(
        entry_ts=inventory_before.open_since or ts,
        exit_ts=ts,
        direction=direction,
        entry_price=entry_price,
        exit_price=exit_price,
        qty=reducing_qty,
        pnl_usd=pnl_usd,
        pnl_bp=pnl_usd / (entry_price * reducing_qty) * 10_000
               if entry_price * reducing_qty > 0 else 0.0,
        maker_fee_bp=maker_fee_bp,
        taker_fee_bp=exit_fee_bp,
        exit_reason=exit_reason,
    )


# ---------------------------------------------------------------------------
# Simulator
# ---------------------------------------------------------------------------

def simulate_market_making(
    aggtrades: pd.DataFrame,
    config: MakerSimConfig,
) -> tuple[list[Trade], dict[str, Any]]:
    """Run the maker simulator over a slice of aggTrades.

    Parameters
    ----------
    aggtrades : pd.DataFrame
        Columns: ``ts`` (UTC datetime), ``price``, ``qty``, ``is_buyer_maker``.
    config : MakerSimConfig
        All simulation parameters.

    Returns
    -------
    (trades, metrics)
        ``trades``  — ``list[Trade]`` compatible with ``run_backtest.py``.
        ``metrics`` — dict with standard gate fields + maker-specific fields.
    """
    t0 = time.time()

    # --- early exit on empty / too-small input ---
    if aggtrades is None or len(aggtrades) == 0:
        return [], {"error": "empty input", "n_trades_in": 0}

    # --- filter window ---
    start = pd.Timestamp(config.start_ts, tz="UTC")
    end = pd.Timestamp(config.end_ts, tz="UTC")
    mask = (aggtrades["ts"] >= start) & (aggtrades["ts"] < end)
    trades_df = aggtrades.loc[mask].sort_values("ts").reset_index(drop=True)

    if config.trade_step > 1:
        trades_df = trades_df.iloc[::config.trade_step].reset_index(drop=True)

    n_trades_in = len(trades_df)
    if n_trades_in < 10:
        return [], {"error": "insufficient trades", "n_trades_in": n_trades_in}

    # --- precompute bars ---
    bars = _resample_bars(trades_df, config.bar_freq)

    # --- state ---
    inventory = empty_inventory(max_inventory=1.0)  # placeholder, reset per price
    adv_state = empty_state()
    active_quote: Quote | None = None

    # open position tracking
    open_entry_ts: pd.Timestamp | None = None
    open_direction: str | None = None
    open_entry_price: float = 0.0
    open_qty: float = 0.0
    open_maker_fee_bp: float = config.maker_fee_bp
    last_quote_ts: pd.Timestamp | None = None

    round_trips: list[RoundTrip] = []
    fills_log: list[FillRecord] = []
    quotes_generated = 0
    quotes_filled = 0

    # continuous-mode stats
    flatten_count = 0
    max_abs_inv_qty = 0.0
    abs_notional_sum = 0.0
    inv_samples = 0
    quotes_log: list[dict] = []

    recent_trades_buffer: list[dict] = []
    RECENT_TRADES_N = 50

    # Track latest bar index for VPVR lookback
    bar_idx = 0
    bar_timestamps = bars.index

    for i in range(n_trades_in):
        row = trades_df.iloc[i]
        ts = row["ts"]
        price = float(row["price"])
        qty = float(row["qty"])
        is_buyer_maker = bool(row["is_buyer_maker"])

        # --- update recent trades buffer ---
        recent_trades_buffer.append({"ts": ts, "price": price, "qty": qty,
                                      "is_buyer_maker": is_buyer_maker})
        if len(recent_trades_buffer) > RECENT_TRADES_N:
            recent_trades_buffer.pop(0)
        recent_trades = pd.DataFrame(recent_trades_buffer)

        # --- advance bar index ---
        while bar_idx < len(bar_timestamps) and bar_timestamps[bar_idx] <= ts:
            bar_idx += 1
        vpvr_bars = bars.iloc[max(0, bar_idx - config.vpvr_bars):bar_idx]

        # --- continuous mode: inventory persists; flatten on risk limits ---
        if config.mode == "continuous":
            # Keep the USD→qty cap current with the market price.
            inventory = replace(
                inventory,
                max_inventory=_max_inventory_qty(price, config.max_inventory_usd),
            )
            if flatten_required(inventory, price, config.max_hold_seconds,
                                config.sl_bp, current_ts=ts):
                if inventory.is_at_limit:
                    flatten_reason = "limit"
                elif inventory.open_since is not None and (
                    ts - inventory.open_since
                ).total_seconds() >= config.max_hold_seconds:
                    flatten_reason = "time"
                else:
                    flatten_reason = "sl"
                flatten_qty = abs(inventory.net_qty)
                round_trips.append(_reducing_roundtrip(
                    inventory, price, ts, flatten_qty,
                    maker_fee_bp=config.maker_fee_bp,
                    exit_fee_bp=config.taker_fee_bp,
                    exit_reason=f"flatten_{flatten_reason}",
                ))
                fills_log.append(FillRecord(
                    ts=ts, side="sell" if inventory.net_qty > 0 else "buy",
                    price=price, qty=flatten_qty,
                    fee_bp=config.taker_fee_bp, is_maker=False,
                ))
                inventory = _apply_signed_fill(
                    inventory, -inventory.net_qty, price, ts,
                )
                flatten_count += 1

            max_abs_inv_qty = max(max_abs_inv_qty, abs(inventory.net_qty))
            abs_notional_sum += abs(inventory.net_qty) * price
            inv_samples += 1

        # --- check exit on open position ---
        if open_direction is not None:
            should_exit = False
            exit_reason = ""

            # TP / SL
            if open_direction == "long":
                pnl_bp = (price - open_entry_price) / open_entry_price * 10_000
            else:
                pnl_bp = (open_entry_price - price) / open_entry_price * 10_000

            net_pnl_bp = pnl_bp - open_maker_fee_bp - config.taker_fee_bp

            if net_pnl_bp >= config.tp_bp:
                should_exit = True
                exit_reason = "tp"
            elif net_pnl_bp <= -config.sl_bp:
                should_exit = True
                exit_reason = "sl"

            # Time-based exit
            if not should_exit and open_entry_ts is not None:
                held = (ts - open_entry_ts).total_seconds()
                if held >= config.max_hold_seconds:
                    should_exit = True
                    exit_reason = "time"

            if should_exit:
                # Close at current price (taker)
                if open_direction == "long":
                    exit_price = price
                    pnl_usd = (exit_price - open_entry_price) * open_qty
                else:
                    exit_price = price
                    pnl_usd = (open_entry_price - exit_price) * open_qty

                rt = RoundTrip(
                    entry_ts=open_entry_ts,
                    exit_ts=ts,
                    direction=open_direction,
                    entry_price=open_entry_price,
                    exit_price=exit_price,
                    qty=open_qty,
                    pnl_usd=pnl_usd,
                    pnl_bp=pnl_usd / (open_entry_price * open_qty) * 10_000
                           if open_entry_price * open_qty > 0 else 0.0,
                    maker_fee_bp=open_maker_fee_bp,
                    taker_fee_bp=config.taker_fee_bp,
                    exit_reason=exit_reason,
                )
                round_trips.append(rt)
                fills_log.append(FillRecord(
                    ts=ts, side="sell" if open_direction == "long" else "buy",
                    price=exit_price, qty=open_qty,
                    fee_bp=config.taker_fee_bp, is_maker=False,
                ))

                open_direction = None
                open_entry_ts = None
                open_entry_price = 0.0
                open_qty = 0.0
                inventory = empty_inventory(
                    max_inventory=_max_inventory_qty(price, config.max_inventory_usd),
                )
                # Reset position, keep adversary state decaying

        # --- skip quote generation if position open (one-position model) ---
        if open_direction is not None:
            continue

        # --- decay adverse-selection penalty ---
        adv_state = decay_penalty(adv_state, ts, AdverseSelectionParams(
            fill_penalty_bp=config.fill_penalty_bp,
            penalty_decay_per_second=config.penalty_decay_per_second,
            sweep_threshold=config.sweep_threshold,
            sweep_cooldown_seconds=config.sweep_cooldown_seconds,
            max_penalty_bp=config.max_penalty_bp,
            expected_sweep_cost_bp=config.expected_sweep_cost_bp,
        ))

        if not is_quoting_allowed(adv_state, ts):
            continue

        # --- build market snapshot ---
        bid_est, ask_est = _infer_book(
            price, is_buyer_maker,
            config.spread_estimate_ticks, config.tick_size,
        )
        snap = MarketSnapshot(
            timestamp=ts,
            bid_price=bid_est,
            ask_price=ask_est,
            bid_volume=qty if is_buyer_maker else qty,
            ask_volume=qty if not is_buyer_maker else qty,
            last_price=price,
            recent_trades=recent_trades,
            bars=vpvr_bars if len(vpvr_bars) > 5 else bars.tail(config.vpvr_bars),
        )

        # --- compute fair value ---
        fv = compute_fair_value(
            snap,
            vwap_lookback=config.vwap_lookback,
            vpvr_bars=config.vpvr_bars,
        )

        # --- compute sigma ---
        sig = rolling_sigma(recent_trades, config.sigma_window_seconds)

        # --- max inventory in qty ---
        max_inv_qty = _max_inventory_qty(fv.composite, config.max_inventory_usd)
        if max_inv_qty <= 0:
            continue
        if config.mode == "continuous":
            # Inventory persists across ticks; keep the qty cap current.
            inventory = replace(inventory, max_inventory=max_inv_qty)
        else:
            # Legacy one-position model: flat between round-trips.
            inventory = InventoryState(
                net_qty=0.0, gross_qty=0.0, avg_price=0.0,
                notional_usd=0.0, last_fill_ts=None,
                max_inventory=max_inv_qty,
            )

        # --- reservation price ---
        rp = reservation_price(
            fair_value=fv.composite,
            inventory_qty=inventory.net_qty if config.mode == "continuous" else 0.0,
            sigma=sig,
            time_remaining=config.horizon_seconds,
            gamma=config.gamma,
        )

        # --- spread model ---
        base_spread_bp = config.base_spread_bp
        vol_spread_coeff = config.vol_spread_coeff
        if config.spread_mode == "optimal":
            # A-S closed-form half-spread; vol already priced in, so the
            # heuristic vol component is disabled to avoid double-counting.
            hs_frac = optimal_half_spread(
                sigma=sig,
                time_remaining=config.horizon_seconds,
                params=OptimalSpreadParams(
                    gamma=config.gamma,
                    kappa=config.kappa,
                    horizon_seconds=config.horizon_seconds,
                ),
            )
            base_spread_bp = hs_frac * 10_000.0
            vol_spread_coeff = 0.0

        # --- generate quote ---
        quote = generate_quotes(
            reservation_price=rp,
            sigma=sig,
            inventory_state=inventory,
            adverse_selection_penalty_bp=adv_state.penalty_bp,
            mcls_size_multiplier=1.0,  # MCLS integration at live layer
            params=QuotingParams(
                base_spread_bp=base_spread_bp,
                min_spread_ticks=config.spread_estimate_ticks,
                size_usd=config.size_usd,
                inventory_skew_factor=config.inventory_skew_factor,
                vol_spread_coeff=vol_spread_coeff,
                tick_size=config.tick_size,
            ),
            timestamp=ts,
        )

        if quote is None:
            continue

        quotes_generated += 1
        active_quote = quote
        last_quote_ts = ts

        if config.record_quotes:
            quotes_log.append({
                "ts": ts,
                "fair_value": fv.composite,
                "inventory_qty": inventory.net_qty,
                "sigma": sig,
                "reservation_price": rp,
                "bid_price": quote.bid_price,
                "ask_price": quote.ask_price,
            })

        # --- check if this trade would have filled our quote ---
        fill_side = None
        fill_price = 0.0

        # If trade prints at or below our bid → bid hit (we buy)
        if quote.bid_price > 0 and price <= quote.bid_price:
            fill_side = BID_HIT
            fill_price = quote.bid_price
        # If trade prints at or above our ask → ask lifted (we sell)
        elif quote.ask_price > 0 and price >= quote.ask_price:
            fill_side = ASK_LIFTED
            fill_price = quote.ask_price

        if fill_side is not None:
            quotes_filled += 1

            # Position size
            fill_qty = config.size_usd / fill_price if fill_price > 0 else 0.0

            if config.mode == "continuous":
                # Inventory-aware bookkeeping: signed fill updates the
                # persistent inventory; the reducing portion realizes PnL.
                signed_qty = fill_qty if fill_side == BID_HIT else -fill_qty
                if (
                    inventory.net_qty != 0.0
                    and (inventory.net_qty > 0) != (signed_qty > 0)
                ):
                    # Reducing side: cap the fill at current inventory.
                    # If the residual would be dust (<10% of a lot), take
                    # the whole position — dust leftovers get a meaningless
                    # blended basis and distort per-trade PnL.
                    reducing_qty = min(abs(signed_qty), abs(inventory.net_qty))
                    if ((abs(inventory.net_qty) - reducing_qty) * fill_price
                            < 0.1 * config.size_usd):
                        reducing_qty = abs(inventory.net_qty)
                    signed_qty = (reducing_qty if signed_qty > 0
                                  else -reducing_qty)
                    fill_qty = reducing_qty
                    round_trips.append(_reducing_roundtrip(
                        inventory, fill_price, ts, reducing_qty,
                        maker_fee_bp=config.maker_fee_bp,
                        exit_fee_bp=config.maker_fee_bp,  # maker exit via quote
                        exit_reason="spread_capture",
                    ))
                inventory = _apply_signed_fill(
                    inventory, signed_qty, fill_price, ts,
                )
            else:
                # Legacy one-position model: open position, stop quoting.
                if fill_side == BID_HIT:
                    open_direction = "long"
                else:
                    open_direction = "short"

                open_entry_ts = ts
                open_entry_price = fill_price
                open_qty = fill_qty
                open_maker_fee_bp = config.maker_fee_bp

            fills_log.append(FillRecord(
                ts=ts,
                side="buy" if fill_side == BID_HIT else "sell",
                price=fill_price, qty=fill_qty,
                fee_bp=config.maker_fee_bp, is_maker=True,
            ))

            # --- adverse selection update ---
            adv_state = on_fill(adv_state, fill_side, ts, AdverseSelectionParams(
                fill_penalty_bp=config.fill_penalty_bp,
                penalty_decay_per_second=config.penalty_decay_per_second,
                sweep_threshold=config.sweep_threshold,
                sweep_cooldown_seconds=config.sweep_cooldown_seconds,
                max_penalty_bp=config.max_penalty_bp,
                expected_sweep_cost_bp=config.expected_sweep_cost_bp,
            ))

    # --- force-close any remaining position at last price ---
    if open_direction is not None and n_trades_in > 0:
        last_price = float(trades_df.iloc[-1]["price"])
        last_ts = trades_df.iloc[-1]["ts"]
        if open_direction == "long":
            pnl_usd = (last_price - open_entry_price) * open_qty
        else:
            pnl_usd = (open_entry_price - last_price) * open_qty
        round_trips.append(RoundTrip(
            entry_ts=open_entry_ts,
            exit_ts=last_ts,
            direction=open_direction,
            entry_price=open_entry_price,
            exit_price=last_price,
            qty=open_qty,
            pnl_usd=pnl_usd,
            pnl_bp=pnl_usd / (open_entry_price * open_qty) * 10_000
                   if open_entry_price * open_qty > 0 else 0.0,
            maker_fee_bp=open_maker_fee_bp,
            taker_fee_bp=config.taker_fee_bp,
            exit_reason="eod",
        ))

    # --- continuous mode: force-flatten any residual inventory at EOD ---
    if config.mode == "continuous" and not inventory.is_flat and n_trades_in > 0:
        last_price = float(trades_df.iloc[-1]["price"])
        last_ts = trades_df.iloc[-1]["ts"]
        round_trips.append(_reducing_roundtrip(
            inventory, last_price, last_ts, abs(inventory.net_qty),
            maker_fee_bp=config.maker_fee_bp,
            exit_fee_bp=config.taker_fee_bp,
            exit_reason="eod",
        ))
        inventory = _apply_signed_fill(
            inventory, -inventory.net_qty, last_price, last_ts,
        )

    elapsed = time.time() - t0

    # --- compute metrics ---
    metrics = _compute_metrics(round_trips, fills_log, quotes_generated,
                               quotes_filled, n_trades_in, elapsed, config)
    metrics["mode"] = config.mode
    metrics["spread_mode"] = config.spread_mode
    if config.mode == "continuous":
        metrics.update({
            "flatten_count": flatten_count,
            "max_abs_inventory_qty": max_abs_inv_qty,
            "avg_abs_inventory_usd": abs_notional_sum / max(1, inv_samples),
            "realized_pnl_usd": float(sum(rt.pnl_usd for rt in round_trips)),
        })
    if config.record_quotes:
        metrics["quotes"] = quotes_log
    trades = [roundtrip_to_trade(rt) for rt in round_trips]

    return trades, metrics


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

def _compute_metrics(
    round_trips: list[RoundTrip],
    fills_log: list[FillRecord],
    quotes_generated: int,
    quotes_filled: int,
    n_trades_in: int,
    elapsed: float,
    config: MakerSimConfig,
) -> dict[str, Any]:
    """Compute gate-compatible + maker-specific metrics."""
    n_rt = len(round_trips)

    if n_rt == 0:
        return {
            "n_trades": 0,
            "sharpe_daily": 0.0,
            "annualized_return": 0.0,
            "max_drawdown_pct": 0.0,
            "profit_factor": 0.0,
            "fill_rate": quotes_filled / max(1, quotes_generated),
            "maker_ratio": 0.0,
            "avg_spread_bp": 0.0,
            "quotes_generated": quotes_generated,
            "quotes_filled": quotes_filled,
            "n_trades_in": n_trades_in,
            "elapsed_seconds": elapsed,
        }

    pnl_bp = np.array([rt.pnl_bp for rt in round_trips])
    pnl_usd = np.array([rt.pnl_usd for rt in round_trips])

    # Exit-reason breakdown
    exit_reasons: dict[str, int] = {}
    for rt in round_trips:
        exit_reasons[rt.exit_reason] = exit_reasons.get(rt.exit_reason, 0) + 1

    # Sharpe (per-trade, annualised by ~1000 trades/day for BTC 24h)
    if len(pnl_bp) > 1 and np.std(pnl_bp, ddof=1) > 0:
        trades_per_day = max(1, n_rt)  # rough
        sharpe = float(np.mean(pnl_bp) / np.std(pnl_bp, ddof=1) * np.sqrt(trades_per_day))
    else:
        sharpe = 0.0

    # Annualised return (rough)
    total_pnl_bp = float(np.sum(pnl_bp))
    n_days = max(1, (
        pd.Timestamp(config.end_ts, tz="UTC") -
        pd.Timestamp(config.start_ts, tz="UTC")
    ).total_seconds() / 86400)
    ann_return = total_pnl_bp / 10_000.0 / n_days * 365

    # Drawdown
    cum = np.cumsum(pnl_usd)
    running_max = np.maximum.accumulate(cum)
    dd = cum - running_max
    max_dd_pct = float(dd.min()) if len(dd) > 0 else 0.0

    # Profit factor
    gains = pnl_usd[pnl_usd > 0].sum()
    losses = abs(pnl_usd[pnl_usd < 0].sum())
    pf = float(gains / losses) if losses > 0 else float("inf")

    # Maker ratio
    maker_fills = sum(1 for f in fills_log if f.is_maker)
    total_fills = len(fills_log)
    maker_ratio = maker_fills / total_fills if total_fills > 0 else 0.0

    # Fill rate
    fill_rate = quotes_filled / max(1, quotes_generated)

    # Avg spread (from fills — entry fills are maker, at quote price)
    avg_entry_bp = np.mean([f.fee_bp for f in fills_log if f.is_maker]) if maker_fills > 0 else 0.0

    return {
        # Gate-compatible fields
        "n_trades": n_rt,
        "sharpe_daily": sharpe,
        "annualized_return": ann_return,
        "max_drawdown_pct": max_dd_pct,
        "profit_factor": pf,
        "total_return_pct": total_pnl_bp / 100.0,
        # Maker-specific
        "fill_rate": fill_rate,
        "maker_ratio": maker_ratio,
        "avg_maker_fee_bp": avg_entry_bp,
        "quotes_generated": quotes_generated,
        "quotes_filled": quotes_filled,
        "n_trades_in": n_trades_in,
        "exit_reasons": exit_reasons,
        "avg_pnl_bp": float(np.mean(pnl_bp)),
        "median_pnl_bp": float(np.median(pnl_bp)),
        "win_rate": float(np.mean(pnl_bp > 0)),
        "elapsed_seconds": elapsed,
        # ---- Kelly sizing ----
        "kelly_multiplier": _safe_kelly(pnl_bp),
        "kelly_mean_edge_bp": float(np.mean(pnl_bp)),
        "kelly_std_edge_bp": float(np.std(pnl_bp, ddof=1)) if n_rt > 1 else 0.0,
        # ---- Tail risk (VaR / CVaR) ----
        "var_95_bp": _safe_tail(pnl_bp, "var_95"),
        "var_99_bp": _safe_tail(pnl_bp, "var_99"),
        "cvar_95_bp": _safe_tail(pnl_bp, "cvar_95"),
        "cvar_99_bp": _safe_tail(pnl_bp, "cvar_99"),
        "worst_case_bp": float(abs(np.min(pnl_bp))) if n_rt > 0 else 0.0,
        "max_consecutive_losses": _safe_max_losses(pnl_bp),
    }


def _safe_kelly(pnl_bp: np.ndarray) -> float:
    """Compute Kelly sizing multiplier, fail-safe."""
    try:
        from _shared.market_making.kelly_sizing import adaptive_kelly_multiplier
        return adaptive_kelly_multiplier(pnl_bp.tolist())
    except Exception:
        return 1.0


def _safe_tail(pnl_bp: np.ndarray, metric: str) -> float:
    """Compute tail-risk metric, fail-safe."""
    try:
        from _shared.market_making.tail_risk import compute_tail_risk
        tr = compute_tail_risk(pnl_bp.tolist())
        return getattr(tr, metric + "_bp")
    except Exception:
        return 0.0


def _safe_max_losses(pnl_bp: np.ndarray) -> int:
    """Compute max consecutive losses, fail-safe."""
    try:
        from _shared.market_making.tail_risk import max_consecutive_losses
        return max_consecutive_losses(pnl_bp.tolist())
    except Exception:
        return 0
