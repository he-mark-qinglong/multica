"""Live fill-engine for SMA-35012 paper-trading harness.

Subscribes to Binance public WebSocket kline streams for the configured
pair (no auth, no testnet needed for market data). For each 30m bar close:

  1. Append bar to in-memory history per symbol
  2. Re-run strategy.run_backtest on the combined warmup-history + live
     history to produce a fresh trade list
  3. Diff against the previously-processed trade list to find NEW trades
  4. For each new trade: apply _shared.execution.cost_model, write
     trades.jsonl + equity_curve.csv + daily_metrics.csv rows
  5. Evaluate kill criteria (smark absolute + issue-body thresholds)
  6. If KILL: emit KILL row to system.jsonl + halt

Design choices:

  * Public WS only (wss://fstream.binance.com/ws/...). No testnet auth
    needed because we're reading market data, not placing orders.
  * Deterministic warmup: runs strategy once on the existing historical
    parquet to establish `trades_warmup` baseline. Live bars extend the
    history; any new trade must have entry_ts > last warmup entry_ts.
  * Strategy re-runs on each close-bar tick. The strategy is O(n*window)
    in the rolling VPVR; on ~80k bars + 1 live bar this is ~5M ops,
    well under a second on a modern CPU.
  * Bounded runtime via --window-min flag (default 30 minutes). The
    engine exits cleanly at the first of: window expiry, KILL trigger,
    or process signal.
  * No real orders are sent. trades.jsonl entries are SIMULATED fills
    against the closing bar's price plus a slip model from
    _shared.execution.cost_model.
"""
from __future__ import annotations

import csv
import json
import math
import signal
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

# Public Binance WS endpoint. NO auth required for market data.
WS_BASE = "wss://fstream.binance.com"

QUANT_LOOP_ROOT = Path("/home/smark/multica/quant-loop")
sys.path.insert(0, str(QUANT_LOOP_ROOT))


# ---------------------------------------------------------------------------
# Kline ingest
# ---------------------------------------------------------------------------

@dataclass
class KlineBar:
    """One 30m kline from the WS feed."""
    symbol: str
    open_time_ms: int
    close_time_ms: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    quote_volume: float
    is_closed: bool


def _parse_kline_msg(msg: dict) -> Optional[KlineBar]:
    """Parse a single Binance kline WS message. Returns None if invalid."""
    try:
        # Multi-stream wraps in {"stream":..., "data":{...}}
        data = msg.get("data", msg)
        k = data.get("k") or {}
        if not k:
            return None
        return KlineBar(
            symbol=str(data["s"]).upper(),
            open_time_ms=int(k["t"]),
            close_time_ms=int(k["T"]),
            open=float(k["o"]),
            high=float(k["h"]),
            low=float(k["l"]),
            close=float(k["c"]),
            volume=float(k["v"]),
            quote_volume=float(k["q"]),
            is_closed=bool(k.get("x", False)),
        )
    except (KeyError, TypeError, ValueError):
        return None


def _ws_url(symbols: list[str], stream: str = "kline_30m") -> str:
    """Build the multi-stream WS URL for the given symbols."""
    parts = [f"{s.lower()}@{stream}" for s in symbols]
    return f"{WS_BASE}/stream?streams={'/'.join(parts)}"


# ---------------------------------------------------------------------------
# Paper account
# ---------------------------------------------------------------------------

@dataclass
class PaperAccount:
    """Tracks equity, daily pnl, peak. Mirrors the kill-criteria inputs."""
    starting_equity_usd: float
    equity_usd: float
    equity_open_today_usd: float
    peak_equity_usd: float
    current_day_utc: str = ""  # YYYY-MM-DD
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    gross_pnl_usd: float = 0.0
    net_pnl_usd: float = 0.0
    fees_usd: float = 0.0
    slippage_usd: float = 0.0
    realized_pnl_pct_per_trade: list = field(default_factory=list)

    def update_on_fill(self, pnl_pct: float, fees_usd: float, slippage_usd: float,
                       trade_date_utc: str) -> None:
        pnl_usd = self.equity_usd * pnl_pct
        self.equity_usd += pnl_usd
        self.net_pnl_usd += pnl_usd
        self.gross_pnl_usd += pnl_usd + fees_usd + slippage_usd
        self.fees_usd += fees_usd
        self.slippage_usd += slippage_usd
        self.realized_pnl_pct_per_trade.append(pnl_pct)
        self.total_trades += 1
        if pnl_pct >= 0:
            self.winning_trades += 1
        else:
            self.losing_trades += 1
        if self.equity_usd > self.peak_equity_usd:
            self.peak_equity_usd = self.equity_usd
        # Day rollover: reset equity_open_today for the new day's first fill
        if trade_date_utc != self.current_day_utc:
            self.current_day_utc = trade_date_utc
            self.equity_open_today_usd = self.equity_usd

    @property
    def max_drawdown_pct(self) -> float:
        if self.peak_equity_usd <= 0:
            return 0.0
        return (self.equity_usd - self.peak_equity_usd) / self.peak_equity_usd * 100.0

    @property
    def daily_return_pct(self) -> float:
        if self.equity_open_today_usd <= 0:
            return 0.0
        return (self.equity_usd - self.equity_open_today_usd) / self.equity_open_today_usd * 100.0

    @property
    def profit_factor_lifetime(self) -> float:
        wins = sum(p for p in self.realized_pnl_pct_per_trade if p > 0)
        losses = sum(-p for p in self.realized_pnl_pct_per_trade if p <= 0)
        if losses == 0:
            return float("inf") if wins > 0 else 0.0
        return float(wins / losses)

    @property
    def win_rate(self) -> float:
        if self.total_trades == 0:
            return 0.0
        return self.winning_trades / self.total_trades


# ---------------------------------------------------------------------------
# Strategy interface (deterministic re-run over combined history)
# ---------------------------------------------------------------------------

STRATEGY_DIR = (QUANT_LOOP_ROOT / "strategies" /
                "vpvr_xs_pairs_30m_funding_filter_eth_sol_v5_loose_20260717")
sys.path.insert(0, str(STRATEGY_DIR))


def _load_strategy_history(symbols: list[str]) -> dict[str, pd.DataFrame]:
    """Load the warmup 30m history for the pair from strategy's data dir.

    Returns a dict of symbol -> DataFrame indexed by openTime (UTC-naive)
    with columns open/high/low/close/volume/quote_volume.
    """
    out = {}
    for s in symbols:
        p = STRATEGY_DIR / "data" / f"{s}__30m.parquet"
        if not p.exists():
            raise FileNotFoundError(f"missing strategy data: {p}")
        df = pd.read_parquet(p).copy()
        if "open_time" in df.columns:
            df["openTime"] = pd.to_datetime(df["open_time"], unit="ms", utc=True).dt.tz_convert(None)
            df = df.set_index("openTime")
        elif isinstance(df.index, pd.DatetimeIndex):
            if df.index.tz is not None:
                df.index = df.index.tz_convert(None)
        keep = [c for c in ("open", "high", "low", "close", "volume", "quote_volume") if c in df.columns]
        out[s] = df[keep].sort_index()
    return out


def _load_strategy_funding(symbols: list[str]) -> dict[str, pd.Series]:
    """Load funding rates for the pair from strategy's data dir."""
    out = {}
    for s in symbols:
        p = STRATEGY_DIR / "data" / f"{s}__funding.parquet"
        if not p.exists():
            out[s] = pd.Series(dtype=float)
            continue
        df = pd.read_parquet(p).copy()
        if "fundingTime" in df.columns:
            df["ts"] = pd.to_datetime(df["fundingTime"], unit="ms", utc=True).dt.tz_convert(None)
            df = df.set_index("ts")
        elif "ts" in df.columns:
            df["ts"] = pd.to_datetime(df["ts"], utc=True).dt.tz_convert(None)
            df = df.set_index("ts")
        if df.index.tz is not None:
            df.index = df.index.tz_convert(None)
        out[s] = df["fundingRate"].sort_index()
    return out


def _run_strategy_for_diff(history: dict[str, pd.DataFrame], funding: dict[str, pd.Series],
                            strategy_cfg: dict, last_entry_ts: Optional[pd.Timestamp],
                            pairs: list[str]) -> list[dict]:
    """Re-run the strategy on the supplied history; return trades whose
    entry_ts > last_entry_ts. Strategy is deterministic; we only need
    the *new* trades (those after the last-processed entry)."""
    from strategy import run_backtest  # imported here to keep module load order clean
    res = run_backtest(history, strategy_cfg, funding=funding)
    new_trades = []
    for pr in res["per_pair"]:
        for t in pr["trades"]:
            et = pd.Timestamp(t["entry_ts"])
            if last_entry_ts is None or et > last_entry_ts:
                new_trades.append(t)
    return new_trades


# ---------------------------------------------------------------------------
# Ledger I/O
# ---------------------------------------------------------------------------

LEDGER_DIR = Path(__file__).resolve().parent / "results-ledger"


def _ensure_headers() -> None:
    LEDGER_DIR.mkdir(parents=True, exist_ok=True)
    daily = LEDGER_DIR / "daily_metrics.csv"
    if not daily.exists() or daily.stat().st_size == 0:
        daily.write_text(
            "date,total_trades,winning_trades,losing_trades,win_rate,"
            "gross_pnl_usd,net_pnl_usd,fees_usd,slippage_usd,equity_usd,"
            "daily_return_pct,rolling_20d_sharpe,rolling_20d_pf,"
            "max_drawdown_pct,max_drawdown_pct_vs_backtest,"
            "profit_factor_lifetime,bootstrap_ci_lo,action,"
            "kill_triggered,kill_reason,notes\n"
        )
    eq = LEDGER_DIR / "equity_curve.csv"
    if not eq.exists() or eq.stat().st_size == 0:
        eq.write_text("ts,equity_usd,equity_return_pct,position_state,signal_state,notes\n")
    sys_log = LEDGER_DIR / "system.jsonl"
    sys_log.touch(exist_ok=True)
    trades_log = LEDGER_DIR / "trades.jsonl"
    trades_log.touch(exist_ok=True)


def _append_trade(trade: dict, pnl_usd: float, pnl_pct_net: float,
                  fees_usd: float, slippage_usd: float,
                  equity_after_usd: float) -> None:
    row = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "ts_exchange": str(trade["exit_ts"]),
        "kind": "fill",
        "client_order_id": f"paper_{int(time.time()*1000)}_{trade['pair'].replace('/', '')}",
        "order_id": None,
        "strategy_id": "vpvr_xs_pairs_30m_funding_filter_eth_sol_v5_loose_20260717",
        "symbol": trade["pair"].replace("/", "_"),
        "side": "buy_a_sell_b" if trade["direction"] == "long_a_short_b" else "sell_a_buy_b",
        "qty": None,                       # sized later when sizing wired; null for now
        "price": float(trade["exit_price_a"]) if trade.get("exit_price_a") else None,
        "notional_usd": abs(pnl_usd) if pnl_usd != 0 else 0.0,
        "commission": round(fees_usd, 6),
        "commission_asset": "USDT",
        "liquidity": "taker",
        "trade_id": None,
        "balance_after": round(equity_after_usd, 6),
        "position_after_qty": 0.0,
        "position_after_avg_price": None,
        "realized_pnl_after": round(pnl_usd, 6),
        "tags": {
            "tf": "30m",
            "edge": "xs_pairs_zscore",
            "pair": trade["pair"],
            "direction": trade["direction"],
            "entry_ts": str(trade["entry_ts"]),
            "entry_price_a": trade["entry_price_a"],
            "entry_price_b": trade["entry_price_b"],
            "exit_price_a": trade["exit_price_a"],
            "exit_price_b": trade["exit_price_b"],
            "z_at_entry": trade["z_at_entry"],
            "z_at_exit": trade["z_at_exit"],
            "funding_ema_at_entry": trade["funding_ema_at_entry"],
            "exit_reason": trade["exit_reason"],
            "bars_held": trade["bars_held"],
            "pnl_pct_gross": trade["pnl_pct"],
            "pnl_pct_net": pnl_pct_net,
            "fees_usd": round(fees_usd, 6),
            "slippage_usd": round(slippage_usd, 6),
        },
    }
    with (LEDGER_DIR / "trades.jsonl").open("a") as fh:
        fh.write(json.dumps(row, default=str) + "\n")


def _append_equity_point(ts: pd.Timestamp, equity_usd: float, position_state: str) -> None:
    row = {
        "ts": ts.isoformat(),
        "equity_usd": round(equity_usd, 6),
        "equity_return_pct": round((equity_usd / 100000.0 - 1.0) * 100.0, 6),
        "position_state": position_state,
        "signal_state": "flat",
        "notes": "",
    }
    with (LEDGER_DIR / "equity_curve.csv").open("a") as fh:
        w = csv.DictWriter(fh, fieldnames=list(row.keys()))
        w.writerow(row)


def _append_system(level: str, kind: str, message: str, **ctx) -> None:
    row = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "level": level,
        "kind": kind,
        "message": message,
        **ctx,
    }
    with (LEDGER_DIR / "system.jsonl").open("a") as fh:
        fh.write(json.dumps(row, default=str) + "\n")


def _append_daily_metrics_row(account: PaperAccount, cfg: dict,
                              kill_state, day_str: str) -> None:
    bt = cfg.get("backtest_expectations", {})
    bt_dd_pct = abs(float(bt.get("backtest_max_dd_pct", 0.0)))
    dd_abs = abs(account.max_drawdown_pct)
    dd_vs_bt = (dd_abs / bt_dd_pct) if bt_dd_pct > 0 else 0.0
    row = {
        "date": day_str,
        "total_trades": account.total_trades,
        "winning_trades": account.winning_trades,
        "losing_trades": account.losing_trades,
        "win_rate": round(account.win_rate, 6),
        "gross_pnl_usd": round(account.gross_pnl_usd, 6),
        "net_pnl_usd": round(account.net_pnl_usd, 6),
        "fees_usd": round(account.fees_usd, 6),
        "slippage_usd": round(account.slippage_usd, 6),
        "equity_usd": round(account.equity_usd, 6),
        "daily_return_pct": round(account.daily_return_pct, 6),
        "rolling_20d_sharpe": 0.0,
        "rolling_20d_pf": round(account.profit_factor_lifetime, 6) if account.profit_factor_lifetime != float("inf") else 999.0,
        "max_drawdown_pct": round(account.max_drawdown_pct, 6),
        "max_drawdown_pct_vs_backtest": round(dd_vs_bt, 6),
        "profit_factor_lifetime": round(account.profit_factor_lifetime, 6) if account.profit_factor_lifetime != float("inf") else 999.0,
        "bootstrap_ci_lo": bt.get("bootstrap_ci_lo", 0.0),
        "action": "HALT" if kill_state.triggered else "RUN",
        "kill_triggered": kill_state.triggered,
        "kill_reason": kill_state.reason,
        "notes": kill_state.trigger_source,
    }
    p = LEDGER_DIR / "daily_metrics.csv"
    write_header = not p.exists() or p.stat().st_size == 0
    with p.open("a", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(row.keys()))
        if write_header:
            w.writeheader()
        w.writerow(row)


# ---------------------------------------------------------------------------
# Main live-loop
# ---------------------------------------------------------------------------

def run_live(cfg: dict, window_min: int = 30, smark_absolute_max_dd: float = 5.0,
             smark_absolute_daily_loss: float = 2.0) -> int:
    """Run the live fill-engine for at most `window_min` minutes.

    Returns:
        0 = clean exit, ≥1 trades processed
        2 = KILL trigger fired (auto-halt)
        3 = WS connection / data feed blocked (ESCALATE)
    """
    from kill_criteria import evaluate as eval_kill, KillState, MetricsSnapshot

    # Inject smark thresholds into cfg so the evaluator picks them up
    cfg.setdefault("kill_criteria", {})
    cfg["kill_criteria"]["smark_absolute_max_dd_pct"] = smark_absolute_max_dd
    cfg["kill_criteria"]["smark_absolute_daily_loss_pct"] = smark_absolute_daily_loss

    _ensure_headers()
    _append_system("INFO", "system", "fill-engine live-loop starting",
                   window_min=window_min, smark_max_dd=smark_absolute_max_dd,
                   smark_daily_loss=smark_absolute_daily_loss,
                   exchange=cfg.get("venue"), pair=cfg.get("pair"))

    pair = cfg["pair"]
    a_sym, b_sym = pair.split("/")
    symbols = [a_sym, b_sym]

    # 1. Warmup: load historical data + run strategy once for baseline
    _append_system("INFO", "warmup", "loading strategy history + funding")
    history = _load_strategy_history(symbols)
    funding = _load_strategy_funding(symbols)

    strategy_cfg = json.loads((STRATEGY_DIR / "config.json").read_text())
    _append_system("INFO", "warmup", "running strategy baseline backtest",
                   n_bars_a=len(history[a_sym]), n_bars_b=len(history[b_sym]))
    warmup_trades = []
    for pr in _run_strategy_for_diff(history, funding, strategy_cfg,
                                      last_entry_ts=None,
                                      pairs=strategy_cfg["pairs"]):
        warmup_trades.append(pr)
    last_entry_ts = (pd.Timestamp(warmup_trades[-1]["entry_ts"])
                     if warmup_trades else None)
    _append_system("INFO", "warmup", "strategy baseline complete",
                   n_warmup_trades=len(warmup_trades),
                   last_entry_ts=str(last_entry_ts) if last_entry_ts else None)

    # 2. Live account state
    starting = float(cfg.get("starting_capital_usd", 100000.0))
    account = PaperAccount(
        starting_equity_usd=starting,
        equity_usd=starting,
        equity_open_today_usd=starting,
        peak_equity_usd=starting,
        current_day_utc=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
    )
    kill_state = KillState()

    # 3. REST-seed: fetch the most recent N 30m bars from Binance public REST
    # so we don't have to wait for the next 30m close to test the engine.
    # Endpoint is unauthenticated public market data.
    import urllib.request
    rest_seed_n = 200   # ~4.2 days of 30m bars; enough to cover recent signals
    _append_system("INFO", "rest_seed", "fetching latest 30m bars via REST",
                   n=rest_seed_n)
    try:
        for s in symbols:
            url = f"https://fapi.binance.com/fapi/v1/klines?symbol={s}&interval=30m&limit={rest_seed_n}"
            with urllib.request.urlopen(url, timeout=10) as resp:
                raw = json.loads(resp.read().decode())
            # raw = [[openTime, open, high, low, close, volume, closeTime, ...], ...]
            rows = []
            for k in raw:
                ts = pd.Timestamp(int(k[0]), unit="ms", tz="UTC").tz_convert(None)
                rows.append({
                    "open": float(k[1]), "high": float(k[2]),
                    "low": float(k[3]), "close": float(k[4]),
                    "volume": float(k[5]), "quote_volume": float(k[7]),
                })
            live_df = pd.DataFrame(rows, index=[r for r in [pd.Timestamp(int(k[0]), unit="ms", tz="UTC").tz_convert(None) for k in raw]])
            live_df.index.name = "openTime"
            # Append only bars newer than the warmup tail
            warmup_tail_ts = history[s].index[-1] if len(history[s]) else pd.Timestamp.min
            newer = live_df[live_df.index > warmup_tail_ts]
            if len(newer):
                history[s] = pd.concat([history[s], newer]).sort_index()
                _append_system("INFO", "rest_seed", f"appended {s} bars",
                               n_new=len(newer), first_ts=str(newer.index[0]),
                               last_ts=str(newer.index[-1]))
        # Re-run strategy on the extended history to capture any new signals
        # generated by the live bars.
        rest_seed_new = _run_strategy_for_diff(history, funding, strategy_cfg,
                                                last_entry_ts=last_entry_ts,
                                                pairs=strategy_cfg["pairs"])
        from _shared.execution.cost_model import apply_cost, BINANCE_FUTURES
        for t in rest_seed_new:
            notional = account.equity_usd * float(strategy_cfg["sizing"]["per_pair_notional_pct"])
            adv_usd = float(history[a_sym]["quote_volume"].iloc[-1]) * 48.0
            if adv_usd <= 0:
                adv_usd = 1e9
            cost_usd = apply_cost(notional_usd=notional, adv_usd=adv_usd,
                                  venue=BINANCE_FUTURES, side="taker",
                                  impact_factor=0.05)
            fees_usd = 0.6 * cost_usd
            slippage_usd = 0.4 * cost_usd
            gross_pnl_pct = float(t["pnl_pct"])
            net_pnl_pct = gross_pnl_pct - (cost_usd / notional if notional > 0 else 0.0)
            exit_ts = pd.Timestamp(t["exit_ts"])
            trade_day = exit_ts.strftime("%Y-%m-%d")
            pnl_usd = account.equity_usd * net_pnl_pct
            account.update_on_fill(net_pnl_pct, fees_usd, slippage_usd, trade_day)
            _append_trade(t, pnl_usd, net_pnl_pct, fees_usd, slippage_usd,
                          account.equity_usd)
            _append_equity_point(exit_ts, account.equity_usd,
                                 position_state="flat_after_fill")
            last_entry_ts = pd.Timestamp(t["entry_ts"])
            _append_system("INFO", "fill", "paper fill (rest_seed)",
                           symbol=t["pair"], direction=t["direction"],
                           pnl_pct_gross=gross_pnl_pct, pnl_pct_net=net_pnl_pct,
                           pnl_usd=round(pnl_usd, 6), fees_usd=round(fees_usd, 6),
                           exit_reason=t["exit_reason"], equity_after=round(account.equity_usd, 6))
        _append_system("INFO", "rest_seed", "rest_seed phase complete",
                       n_new_trades=len(rest_seed_new))
    except Exception as e:
        _append_system("ERROR", "rest_seed", f"REST seed failed: {e!r}; "
                       "continuing with WS only")
        print(f"[fill-engine] REST seed failed: {e!r}", file=sys.stderr)

    # 4. WS subscriber — block until window expires or KILL fires
    import websocket  # websocket-client
    url = _ws_url(symbols, "kline_30m")
    _append_system("INFO", "ws", "connecting", url=url)
    print(f"[fill-engine] connecting WS: {url}")

    deadline = time.time() + window_min * 60
    n_msgs = 0
    n_closed_bars = 0
    n_live_trades = 0
    bars_seen = {s: set() for s in symbols}   # open_time_ms set per symbol

    def _on_message(ws, msg):
        nonlocal n_msgs, n_closed_bars, n_live_trades
        try:
            payload = json.loads(msg)
        except Exception:
            return
        bar = _parse_kline_msg(payload)
        if bar is None or not bar.is_closed:
            return
        n_msgs += 1
        if bar.symbol not in symbols:
            return
        if bar.open_time_ms in bars_seen[bar.symbol]:
            return
        bars_seen[bar.symbol].add(bar.open_time_ms)
        # We need BOTH legs closed at the same open_time before we tick
        a_ready = bar.open_time_ms in bars_seen[a_sym] and a_sym != bar.symbol
        b_ready = bar.open_time_ms in bars_seen[b_sym] and b_sym != bar.symbol
        # If the bar we just got is for leg A, we wait for leg B at the same ts
        # To keep this simple: tick on EITHER leg's close, and re-run; diff covers it.
        # But we need both legs to have a bar at this ts. Track and tick when both ready.
        ts_utc = pd.Timestamp(bar.open_time_ms, unit="ms", tz="UTC").tz_convert(None)
        bar_row = pd.DataFrame([{
            "open": bar.open, "high": bar.high, "low": bar.low,
            "close": bar.close, "volume": bar.volume, "quote_volume": bar.quote_volume,
        }], index=[ts_utc])
        bar_row.index.name = "openTime"
        if ts_utc in history[bar.symbol].index:
            return  # already in history (warmup covered it)
        history[bar.symbol] = pd.concat([history[bar.symbol], bar_row]).sort_index()
        n_closed_bars += 1
        _append_system("INFO", "bar_close", f"closed {bar.symbol} bar",
                       symbol=bar.symbol, ts=ts_utc.isoformat(),
                       close=bar.close, volume=bar.volume)

        # Tick: re-run strategy on combined history
        new_trades = _run_strategy_for_diff(history, funding, strategy_cfg,
                                            last_entry_ts=last_entry_ts,
                                            pairs=strategy_cfg["pairs"])
        from _shared.execution.cost_model import apply_cost, BINANCE_FUTURES
        for t in new_trades:
            # Notional: per-pair-notional-pct of equity (1% default)
            notional = account.equity_usd * float(strategy_cfg["sizing"]["per_pair_notional_pct"])
            # ADV proxy: use the quote_volume of the last 30m bar of leg A
            adv_usd = float(history[a_sym]["quote_volume"].iloc[-1]) * 48.0  # 48 30m bars/day
            if adv_usd <= 0:
                adv_usd = 1e9  # fallback: assume very liquid
            cost_usd = apply_cost(notional_usd=notional, adv_usd=adv_usd,
                                  venue=BINANCE_FUTURES, side="taker",
                                  impact_factor=0.05)
            # Decompose cost into fees vs slip (60/40 split is the model default
            # at 4bps taker + 2bps slip on Binance futures; apply_cost returns
            # round-trip USD). We approximate fees_usd = 60% of round-trip cost.
            fees_usd = 0.6 * cost_usd
            slippage_usd = 0.4 * cost_usd
            gross_pnl_pct = float(t["pnl_pct"])
            # net = gross - (cost/notional). cost is round-trip USD for THIS trade.
            net_pnl_pct = gross_pnl_pct - (cost_usd / notional if notional > 0 else 0.0)
            exit_ts = pd.Timestamp(t["exit_ts"])
            trade_day = exit_ts.strftime("%Y-%m-%d")
            pnl_usd = account.equity_usd * net_pnl_pct
            account.update_on_fill(net_pnl_pct, fees_usd, slippage_usd, trade_day)
            _append_trade(t, pnl_usd, net_pnl_pct, fees_usd, slippage_usd,
                          account.equity_usd)
            _append_equity_point(exit_ts, account.equity_usd,
                                 position_state="flat_after_fill")
            n_live_trades += 1
            last_entry_ts = pd.Timestamp(t["entry_ts"])
            _append_system("INFO", "fill", f"paper fill #{n_live_trades}",
                           symbol=t["pair"], direction=t["direction"],
                           pnl_pct_gross=gross_pnl_pct, pnl_pct_net=net_pnl_pct,
                           pnl_usd=round(pnl_usd, 6), fees_usd=round(fees_usd, 6),
                           exit_reason=t["exit_reason"], equity_after=round(account.equity_usd, 6))

        # 4. Kill criteria evaluation
        bt = cfg.get("backtest_expectations", {})
        m = MetricsSnapshot(
            equity_usd=account.equity_usd,
            equity_open_today_usd=account.equity_open_today_usd,
            peak_equity_usd=account.peak_equity_usd,
            max_drawdown_pct=account.max_drawdown_pct,
            daily_return_pct=account.daily_return_pct,
            profit_factor_lifetime=account.profit_factor_lifetime,
            n_trades=account.total_trades,
            backtest_max_dd_pct=abs(float(bt.get("backtest_max_dd_pct", 0.0))),
        )
        kill_state = eval_kill(m, cfg, kill_state)
        if kill_state.triggered:
            kill_state.at = datetime.now(timezone.utc).isoformat()
            _append_system("CRITICAL", "kill_switch_event",
                           kill_state.reason,
                           from_state="NORMAL", to_state="HALT",
                           trigger=kill_state.trigger_source,
                           context={"n_trades": account.total_trades,
                                    "equity_usd": account.equity_usd,
                                    "max_dd_pct": account.max_drawdown_pct,
                                    "daily_return_pct": account.daily_return_pct,
                                    "pf": account.profit_factor_lifetime})
            _append_daily_metrics_row(account, cfg, kill_state, account.current_day_utc)
            ws.close()
            return 2

    def _on_error(ws, err):
        _append_system("ERROR", "ws_error", str(err))
        print(f"[fill-engine] WS error: {err}", file=sys.stderr)

    def _on_close(ws, code, reason):
        _append_system("INFO", "ws_close", "WS closed", code=code, reason=reason)

    def _on_open(ws):
        _append_system("INFO", "ws_open", "WS connected", url=url)
        print(f"[fill-engine] WS connected")

    ws = websocket.WebSocketApp(
        url,
        on_message=_on_message,
        on_error=_on_error,
        on_close=_on_close,
        on_open=_on_open,
    )

    # Bounded run: install a timer that closes the WS at the deadline
    import threading

    def _on_timer():
        _append_system("INFO", "window_expired",
                       f"window_min={window_min} reached; exiting cleanly",
                       n_closed_bars=n_closed_bars, n_live_trades=n_live_trades)
        try:
            ws.close()
        except Exception:
            pass
    timer = threading.Timer(window_min * 60, _on_timer)
    timer.daemon = True
    timer.start()

    print(f"[fill-engine] running for up to {window_min} minutes "
          f"(deadline epoch={int(deadline)})")
    try:
        ws.run_forever()
    except KeyboardInterrupt:
        _append_system("INFO", "interrupted", "KeyboardInterrupt; exiting")
    finally:
        timer.cancel()
        # Append a final daily-metrics row regardless of how we exited
        _append_daily_metrics_row(account, cfg, kill_state, account.current_day_utc)
        _append_system("INFO", "session_end", "fill-engine session ended",
                       n_msgs=n_msgs, n_closed_bars=n_closed_bars,
                       n_live_trades=n_live_trades,
                       equity_usd=round(account.equity_usd, 6),
                       max_dd_pct=round(account.max_drawdown_pct, 6),
                       daily_return_pct=round(account.daily_return_pct, 6),
                       pf=round(account.profit_factor_lifetime, 6)
                          if account.profit_factor_lifetime != float("inf") else None,
                       kill_triggered=kill_state.triggered)

    if n_live_trades == 0:
        print(f"[fill-engine] no live fills captured in {window_min}-min window "
              f"({n_closed_bars} bar-close events observed).")
    else:
        print(f"[fill-engine] captured {n_live_trades} live paper fills; "
              f"equity=${account.equity_usd:,.2f} dd={account.max_drawdown_pct:.3f}%.")
    return 0 if not kill_state.triggered else 2
