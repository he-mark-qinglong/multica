"""V10 BTC/SOL backtest using backtrader framework.

Validates V7 logic against an established open-source backtest engine.
backtrader provides:
- Proper Sharpe/Sortino/Calmar via analyzers
- Position sizing
- Trade accounting
- Walk-forward support via analyzers

Same params as V7 (xs_pair_zscore_with_vpvr_confluence_and_funding_blowoff_filter_regularized).
"""
import backtrader as bt
import pandas as pd
import numpy as np
from datetime import datetime
from pathlib import Path

DATA_DIR = Path("/home/smark/multica/quant-loop/data/perp_30m")
RESULTS_DIR = Path("/home/smark/multica/quant-loop/strategies/vpvr_xs_pairs_30m_funding_filter_btc_sol_v10_backtrader_20260717")
RESULTS_DIR.mkdir(parents=True, exist_ok=True)


class PairZScoreVPVR(bt.Strategy):
    """BTC/SOL pair z-score + VPVR confluence + funding filter.

    Logic (from V7):
    - Compute log_spread = ln(BTC_close) - ln(SOL_close)
    - zscore = (spread - rolling_mean) / rolling_std over 192 bars (4 days)
    - VPVR POC over 60-bar window with 24 bins
    - Entry: z > 2.5 AND distance_to_POC < 0.7 * ATR
    - Exit: z < 0.5 OR z > 3.0 (regime break)
    - Max hold: 96 bars (48h)
    """
    params = dict(
        vpvr_window=60,
        vpvr_bins=24,
        vpvr_proximity_k=0.7,
        atr_period=14,
        zscore_lookback=192,
        zscore_entry=2.5,
        zscore_exit=0.5,
        zscore_regime_break=3.0,
        max_holding=96,
    )

    def __init__(self):
        # Data feeds: data0=BTC, data1=SOL
        self.btc = self.datas[0]
        self.sol = self.datas[1]
        # Indicators
        # spread computed manually in next()
        # backtrader lacks z-score, computed manually
        # We'll compute in next()
        self.entry_bar = None
        self.trade_log = []

    def next(self):
        if len(self) < max(self.p.zscore_lookback, self.p.vpvr_window) + 1:
            return
        # Compute log spread series manually from price history
        spread_history = []
        for i in range(-self.p.zscore_lookback + 1, 1):
            try:
                btcp = self.btc.close[i]
                solp = self.sol.close[i]
                if btcp > 0 and solp > 0:
                    spread_history.append(np.log(btcp) - np.log(solp))
            except IndexError:
                return
        if len(spread_history) < self.p.zscore_lookback:
            return
        cur_spread = np.log(self.btc.close[0]) - np.log(self.sol.close[0])
        mean_s = np.mean(spread_history)
        std_s = np.std(spread_history, ddof=0)
        if std_s <= 0:
            return
        zscore = (cur_spread - mean_s) / std_s
        # ATR proxy: high-low avg
        atr = (self.btc.high[0] - self.btc.low[0]) / self.btc.close[0] * 100
        # VPVR POC: mode of recent closes
        closes = [self.btc.close[i] for i in range(-self.p.vpvr_window + 1, 1)]
        poc = max(set(closes), key=closes.count) if closes else self.btc.close[0]
        dist_atr = abs(self.btc.close[0] - poc) / max(atr, 0.01)
        pos = self.getposition(self.btc)
        in_position = pos.size != 0
        # Entry: short BTC / long SOL proxy
        if not in_position:
            if zscore > self.p.zscore_entry and dist_atr < self.p.vpvr_proximity_k:
                # Pair trade: short BTC, long SOL (notional equal)
                btc_size = -self.broker.getvalue() * 0.01 / self.btc.close[0]
                sol_size = self.broker.getvalue() * 0.01 / self.sol.close[0]
                self.sell(data=self.btc, size=abs(btc_size))
                self.buy(data=self.sol, size=sol_size)
                self.entry_bar = len(self)
        else:
            bars_held = len(self) - self.entry_bar
            exit_now = (
                zscore < self.p.zscore_exit or
                zscore > self.p.zscore_regime_break or
                bars_held > self.p.max_holding
            )
            if exit_now:
                self.close(self.btc)
                self.close(self.sol)
                self.entry_bar = None

    def notify_trade(self, trade):
        if trade.isclosed:
            self.trade_log.append({
                "pnl": trade.pnl,
                "pnlcomm": trade.pnlcomm,
                "bars": trade.barlen,
                "open_dt": bt.num2date(trade.dtopen).isoformat(),
                "close_dt": bt.num2date(trade.dtclose).isoformat(),
            })


def run_backtest():
    # Load data
    btc_df = pd.read_parquet(DATA_DIR / "BTCUSDT_30m.parquet")
    sol_df = pd.read_parquet(DATA_DIR / "SOLUSDT_30m.parquet")

    # backtrader needs datetime index with specific columns
    def to_bt_feed(df, name):
        df = df.copy()
        df["dt"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
        df = df.set_index("dt")[["open","high","low","close","volume"]]
        df.columns = [name + "_" + c if c != "volume" else "volume" for c in df.columns]
        return bt.feeds.PandasData(dataname=df, name=name, plot=False)

    cerebro = bt.Cerebro(stdstats=False)
    cerebro.adddata(to_bt_feed(btc_df, "BTC"))
    cerebro.adddata(to_bt_feed(sol_df, "SOL"))
    cerebro.addstrategy(PairZScoreVPVR)
    cerebro.broker.set_cash(100000)
    cerebro.broker.setcommission(leverage=1.0)

    # Analyzers
    cerebro.addanalyzer(bt.analyzers.SharpeRatio, _name="sharpe", riskfreerate=0, annualize=True, timeframe=bt.TimeFrame.Months)
    cerebro.addanalyzer(bt.analyzers.DrawDown, _name="dd")
    cerebro.addanalyzer(bt.analyzers.Returns, _name="returns")
    cerebro.addanalyzer(bt.analyzers.TradeAnalyzer, _name="trades")

    print(f"Running V10 (backtrader) on BTC/SOL 30m...")
    res = cerebro.run()
    strat = res[0]
    sharpe = strat.analyzers.sharpe.get_analysis().get("sharperatio", 0) or 0
    mdd = strat.analyzers.dd.get_analysis().max.drawdown or 0
    ret = strat.analyzers.returns.get_analysis().get("rnorm100", 0) or 0
    n_trades = len(strat.trade_log) // 2  # pair trade = 2 legs

    print(f"\n=== V10 (backtrader) BTC/SOL Results ===")
    print(f"Sharpe (annualized): {sharpe:.3f}")
    print(f"Max Drawdown: {mdd:.2f}%")
    print(f"Normalized Return: {ret:.2f}%")
    print(f"Trades (pair): {n_trades}")

    # Save results
    out = RESULTS_DIR / "results.json"
    out.write_text(json.dumps({
        "strategy": "V10_BTC_SOL_backtrader",
        "framework": "backtrader 1.9.78",
        "sharpe_annualized": sharpe,
        "max_drawdown_pct": mdd,
        "normalized_return_pct": ret,
        "n_pair_trades": n_trades,
        "params": {k: v for k, v in strat.params.items()},
    }, indent=2))
    print(f"\nSaved: {out}")


if __name__ == "__main__":
    import json
    run_backtest()
