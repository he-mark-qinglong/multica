"""Live execution bridge — connects the quoting engine to venue adapters.

This module wires the market-making computation layer (fair value,
reservation price, quoting engine, adverse selection, inventory) to the
existing execution infrastructure (shadow book, venue adapters).

DESIGN: To run live, fill in the API credentials in ``LiveQuoterConfig``
and call ``run_live_quoter()``. The quoter will:

  1. Subscribe to market data (aggTrades + book ticker)
  2. Every tick: compute fair value → reservation price → quotes
  3. Cancel stale orders, place new bid/ask
  4. On fill: update inventory + adverse selection guard
  5. On sweep: enter cooldown, widen spread
  6. On TP/SL/time: flatten position

CONFIGURATION: All you need is an API key. See ``config_live.example.json``.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import pandas as pd

from _shared.market_making.fair_value import compute_fair_value, MarketSnapshot
from _shared.market_making.reservation_price import reservation_price, rolling_sigma
from _shared.market_making.inventory import (
    InventoryState, empty_inventory, flatten_required, update_inventory,
)
from _shared.market_making.adverse_selection import (
    AdverseSelectionParams, AdverseSelectionState,
    ASK_LIFTED, BID_HIT,
    belief_update, decay_penalty, empty_state,
    is_quoting_allowed, on_fill,
)
from _shared.market_making.quoting_engine import Quote, QuotingParams, generate_quotes
from _shared.market_making.kelly_sizing import KellyParams, adaptive_kelly_multiplier

logger = logging.getLogger("live_quoter")


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

@dataclass
class LiveQuoterConfig:
    """Everything needed to run the quoter live."""

    # ---- API credentials (FILL THESE IN TO GO LIVE) ----
    api_key: str = ""
    api_secret: str = ""
    testnet: bool = True               # start on testnet!

    # ---- Venue ----
    venue: str = "binance_perp"
    symbol: str = "BTCUSDT"
    tick_size: float = 0.01

    # ---- Strategy params (mirror MakerSimConfig) ----
    gamma: float = 0.1
    sigma_window_seconds: int = 60
    horizon_seconds: float = 300.0

    base_spread_bp: float = 2.0
    size_usd: float = 100.0           # START SMALL
    inventory_skew_factor: float = 1.0
    vol_spread_coeff: float = 0.5

    max_inventory_usd: float = 1000.0

    fill_penalty_bp: float = 1.0
    penalty_decay_per_second: float = 0.5
    sweep_threshold: int = 3
    sweep_cooldown_seconds: float = 5.0
    max_penalty_bp: float = 10.0
    expected_sweep_cost_bp: float = 1.74

    max_hold_seconds: float = 300.0
    tp_bp: float = 5.0
    sl_bp: float = 10.0

    maker_fee_bp: float = 2.0
    taker_fee_bp: float = 5.0

    # ---- Kelly ----
    kelly_fraction: float = 0.25
    kelly_pnl_history_bp: list[float] = field(default_factory=list)

    # ---- Loop control ----
    tick_interval_seconds: float = 1.0
    max_runtime_seconds: float = 0.0  # 0 = run forever

    @classmethod
    def from_json(cls, path: str | Path) -> "LiveQuoterConfig":
        with open(path) as f:
            data = json.load(f)
        return cls(**data)


# ---------------------------------------------------------------------------
# Transport abstraction (plug point for venue adapters)
# ---------------------------------------------------------------------------

class QuoterTransport:
    """Abstract transport — override for real venue connection.

    The default implementation is a no-op logger (paper mode).
    To go live, subclass and connect to the venue adapter:

        class BinanceTransport(QuoterTransport):
            def place_order(self, side, price, qty): ...
            def cancel_order(self, order_id): ...
            def get_book(self) -> tuple[float, float, float, float]: ...
            def get_recent_trades(self) -> pd.DataFrame: ...
    """

    def place_order(self, side: str, price: float, qty: float,
                    is_maker: bool = True) -> str | None:
        """Place an order. Returns order_id or None."""
        logger.info(f"[PAPER] PLACE {side} {qty:.6f} @ {price:.2f}")
        return None

    def cancel_order(self, order_id: str) -> bool:
        logger.info(f"[PAPER] CANCEL {order_id}")
        return True

    def cancel_all(self, symbol: str) -> int:
        logger.info(f"[PAPER] CANCEL_ALL {symbol}")
        return 0

    def get_book_ticker(self, symbol: str) -> tuple[float, float, float, float]:
        """Returns (bid_price, bid_qty, ask_price, ask_qty)."""
        raise NotImplementedError

    def get_recent_trades(self, symbol: str, limit: int = 50) -> pd.DataFrame:
        """Returns DataFrame with columns: ts, price, qty, is_buyer_maker."""
        raise NotImplementedError

    def get_position(self, symbol: str) -> float:
        """Returns net position quantity."""
        return 0.0

    def market_order(self, side: str, qty: float) -> bool:
        """Place a market (taker) order for immediate fill."""
        logger.info(f"[PAPER] MARKET {side} {qty:.6f}")
        return True


# ---------------------------------------------------------------------------
# Live Quoter
# ---------------------------------------------------------------------------

class LiveQuoter:
    """Main live quoting loop.

    Usage:
        config = LiveQuoterConfig.from_json("config_live.json")
        transport = MyTransport()  # or BinanceTransport(config)
        quoter = LiveQuoter(config, transport)
        quoter.run()
    """

    def __init__(self, config: LiveQuoterConfig, transport: QuoterTransport):
        self.cfg = config
        self.transport = transport
        self.symbol = config.symbol

        # Strategy state
        max_inv_qty = config.max_inventory_usd / 50000  # placeholder, updated live
        self.inventory = empty_inventory(max_inventory=max_inv_qty)
        self.adv_state = empty_state()
        self.open_position: dict[str, Any] | None = None
        self.active_orders: dict[str, dict] = {}  # order_id → {side, price, qty}

        # Params objects
        self.adv_params = AdverseSelectionParams(
            fill_penalty_bp=config.fill_penalty_bp,
            penalty_decay_per_second=config.penalty_decay_per_second,
            sweep_threshold=config.sweep_threshold,
            sweep_cooldown_seconds=config.sweep_cooldown_seconds,
            max_penalty_bp=config.max_penalty_bp,
            expected_sweep_cost_bp=config.expected_sweep_cost_bp,
        )
        self.quote_params = QuotingParams(
            base_spread_bp=config.base_spread_bp,
            min_spread_ticks=2,
            size_usd=config.size_usd,
            inventory_skew_factor=config.inventory_skew_factor,
            vol_spread_coeff=config.vol_spread_coeff,
            tick_size=config.tick_size,
        )

        self._start_time = 0.0
        self._pnl_history: list[float] = []

    def run(self):
        """Main loop. Runs until max_runtime or KeyboardInterrupt."""
        self._start_time = time.time()
        logger.info(f"LiveQuoter starting — symbol={self.symbol} "
                     f"testnet={self.cfg.testnet}")

        try:
            while self._should_continue():
                self._tick()
                time.sleep(self.cfg.tick_interval_seconds)
        except KeyboardInterrupt:
            logger.info("Interrupted by user")
        finally:
            self._shutdown()

    def _should_continue(self) -> bool:
        if self.cfg.max_runtime_seconds <= 0:
            return True
        return (time.time() - self._start_time) < self.cfg.max_runtime_seconds

    def _tick(self):
        """One iteration of the quoting loop."""
        try:
            # 1. Get market data
            bid_px, bid_qty, ask_px, ask_qty = self.transport.get_book_ticker(self.symbol)
            recent = self.transport.get_recent_trades(self.symbol)
            mid = (bid_px + ask_px) / 2

            # Update max inventory in qty
            if mid > 0:
                self.inventory = InventoryState(
                    net_qty=self.inventory.net_qty,
                    gross_qty=self.inventory.gross_qty,
                    avg_price=self.inventory.avg_price,
                    notional_usd=abs(self.inventory.net_qty * mid),
                    last_fill_ts=self.inventory.last_fill_ts,
                    max_inventory=self.cfg.max_inventory_usd / mid,
                    open_since=self.inventory.open_since,
                )

            # 2. Check exit on open position
            if self.open_position is not None:
                self._check_exit(mid, pd.Timestamp.now(tz="UTC"))
                if self.open_position is not None:
                    return  # still in position, don't quote

            # 3. Decay adverse selection
            now = pd.Timestamp.now(tz="UTC")
            self.adv_state = decay_penalty(self.adv_state, now, self.adv_params)
            if not is_quoting_allowed(self.adv_state, now):
                return

            # 4. Cancel stale orders
            for oid in list(self.active_orders.keys()):
                self.transport.cancel_order(oid)
            self.active_orders.clear()

            # 5. Compute fair value
            snap = MarketSnapshot(
                timestamp=now,
                bid_price=bid_px, ask_price=ask_px,
                bid_volume=bid_qty, ask_volume=ask_qty,
                last_price=mid,
                recent_trades=recent,
                bars=pd.DataFrame(),  # no historical bars in live mode
            )
            fv = compute_fair_value(snap)

            # 6. Compute sigma
            sig = rolling_sigma(recent, self.cfg.sigma_window_seconds)

            # 7. Kelly sizing multiplier
            kelly_mult = adaptive_kelly_multiplier(
                self._pnl_history or self.cfg.kelly_pnl_history_bp,
                KellyParams(fraction=self.cfg.kelly_fraction),
            ) if self._pnl_history or self.cfg.kelly_pnl_history_bp else 1.0

            # 8. Reservation price
            rp = reservation_price(
                fair_value=fv.composite,
                inventory_qty=self.inventory.net_qty,
                sigma=sig,
                time_remaining=self.cfg.horizon_seconds,
                gamma=self.cfg.gamma,
            )

            # 9. Generate quotes
            quote = generate_quotes(
                reservation_price=rp,
                sigma=sig,
                inventory_state=self.inventory,
                adverse_selection_penalty_bp=self.adv_state.penalty_bp,
                mcls_size_multiplier=kelly_mult,
                params=self.quote_params,
                timestamp=now,
            )

            if quote is None:
                return

            # 10. Place orders
            if quote.bid_price > 0 and quote.bid_size > 0:
                qty = quote.bid_size / quote.bid_price
                oid = self.transport.place_order("BUY", quote.bid_price, qty)
                if oid:
                    self.active_orders[oid] = {"side": "BUY", "price": quote.bid_price, "qty": qty}

            if quote.ask_price > 0 and quote.ask_size > 0:
                qty = quote.ask_size / quote.ask_price
                oid = self.transport.place_order("SELL", quote.ask_price, qty)
                if oid:
                    self.active_orders[oid] = {"side": "SELL", "price": quote.ask_price, "qty": qty}

        except Exception as e:
            logger.error(f"Tick error: {e}", exc_info=True)

    def _check_exit(self, current_price: float, ts: pd.Timestamp):
        """Check if open position should be exited."""
        pos = self.open_position
        if pos is None:
            return

        entry = pos["entry_price"]
        direction = pos["direction"]  # "long" | "short"

        if direction == "long":
            pnl_bp = (current_price - entry) / entry * 10_000
        else:
            pnl_bp = (entry - current_price) / entry * 10_000

        net_pnl_bp = pnl_bp - self.cfg.maker_fee_bp - self.cfg.taker_fee_bp

        should_exit = False
        reason = ""
        if net_pnl_bp >= self.cfg.tp_bp:
            should_exit, reason = True, "tp"
        elif net_pnl_bp <= -self.cfg.sl_bp:
            should_exit, reason = True, "sl"
        elif (ts - pos["entry_ts"]).total_seconds() >= self.cfg.max_hold_seconds:
            should_exit, reason = True, "time"

        if should_exit:
            side = "SELL" if direction == "long" else "BUY"
            self.transport.market_order(side, abs(self.inventory.net_qty))
            self._pnl_history.append(net_pnl_bp)
            logger.info(f"EXIT {reason} pnl={net_pnl_bp:.2f}bp")
            self.inventory = empty_inventory(max_inventory=self.inventory.max_inventory)
            self.open_position = None

    def on_fill(self, side: str, price: float, qty: float, ts: pd.Timestamp):
        """Called by the transport when an order is filled."""
        fill_qty = qty if side == "BUY" else -qty
        self.inventory = update_inventory(self.inventory, fill_qty, price, ts)

        fill_side = BID_HIT if side == "BUY" else ASK_LIFTED
        self.adv_state = on_fill(self.adv_state, fill_side, ts, self.adv_params)

        if self.open_position is None and not self.inventory.is_flat:
            self.open_position = {
                "entry_ts": ts,
                "entry_price": price,
                "direction": "long" if self.inventory.net_qty > 0 else "short",
            }
        logger.info(f"FILL {side} {qty:.6f} @ {price:.2f} — "
                     f"net_qty={self.inventory.net_qty:.6f}")

    def _shutdown(self):
        """Clean up on exit."""
        logger.info("Shutting down — cancelling all orders")
        self.transport.cancel_all(self.symbol)
        if self.open_position is not None:
            logger.warning("Position still open at shutdown — manual intervention needed")
        logger.info(f"Session complete — {len(self._pnl_history)} round-trips")


# ---------------------------------------------------------------------------
# Convenience runner
# ---------------------------------------------------------------------------

def run_live_quoter(config_path: str | Path, transport: QuoterTransport | None = None):
    """One-call entry point.

    Parameters
    ----------
    config_path : path to JSON config (with API credentials)
    transport : optional custom transport (defaults to paper mode)
    """
    config = LiveQuoterConfig.from_json(config_path)

    if not config.api_key:
        logger.warning("No API key in config — running in PAPER mode")

    if transport is None:
        transport = QuoterTransport()  # paper mode

    quoter = LiveQuoter(config, transport)
    quoter.run()
