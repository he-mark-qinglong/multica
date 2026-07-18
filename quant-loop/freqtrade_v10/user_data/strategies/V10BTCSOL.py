"""V10: BTC/SOL pair z-score + VPVR + funding filter, freqtrade framework version.

Same logic as V7 (xs_pair_zscore_with_vpvr_confluence_and_funding_blowoff_filter_regularized)
but implemented as a freqtrade IStrategy. This validates V7's custom code against
the freqtrade backtesting engine and gives us proper Sharpe/Sortino/Calmar analysis.

Key params (V7-equivalent):
- zscore_lookback_bars = 192 (4 days on 30m)
- zscore_entry_threshold = 2.5
- zscore_exit_threshold = 0.5
- max_holding_bars = 96 (48 hours)
- funding_filter_threshold = 0.0003
"""
# pragma pylint: disable=missing-docstring, invalid-name, pointless-string-statement
import numpy as np
import pandas as pd
from datetime import datetime, timezone
from pandas import DataFrame
from freqtrade.strategy import IStrategy
from technical import qtpylib


class V10_BTCSOL_PairZScore_VPVR(IStrategy):
    """BTC/SOL pair z-score with VPVR confluence + funding filter, freqtrade-validated."""

    INTERFACE_VERSION = 3
    timeframe = "30m"
    can_short = True
    process_only_new_candles = False
    startup_candle_count = 200

    # V7-equivalent params
    vpvr_window_bars = 60
    vpvr_n_bins = 24
    vpvr_proximity_atr_k = 0.7
    atr_period = 14
    zscore_lookback_bars = 192
    zscore_entry_threshold = 2.5
    zscore_exit_threshold = 0.5
    regime_switch_zscore_threshold = 3.0
    max_holding_bars = 96
    funding_8h_ema_window = 8
    funding_filter_threshold = 0.0003

    # Risk
    stake_currency = "USDT"
    stoploss = -0.05
    minimal_roi = {"0": 100}  # disable ROI-based exit; rely on signals

    # Disable shorting protection (we need both legs)
    position_adjustment_enable = False
    max_open_trades = 1
    timeframe_to_minutes = 30

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        """Compute pair z-score, VPVR POC, ATR, funding EMA."""
        pair = "SOLUSDT"
        if metadata["pair"] == "BTCUSDT/SOLUSDT":
            # Informative pair
            sol = self.dp.get_pair_dataframe(pair, self.timeframe)
            sol = sol.rename(columns=lambda c: f"sol_{c}" if c != "date" else "date")
            dataframe = dataframe.merge(sol, on="date", how="inner")

            # Log spread
            dataframe["log_spread"] = np.log(dataframe["close"]) - np.log(dataframe["sol_close"])

            # Z-score (V7 params: 192-bar lookback)
            lookback = self.zscore_lookback_bars
            rolling_mean = dataframe["log_spread"].rolling(lookback).mean()
            rolling_std = dataframe["log_spread"].rolling(lookback).std()
            dataframe["zscore"] = (dataframe["log_spread"] - rolling_mean) / rolling_std

            # ATR for VPVR proximity filter
            high_low = dataframe["high"] - dataframe["low"]
            high_close = (dataframe["high"] - dataframe["close"].shift(1)).abs()
            low_close = (dataframe["low"] - dataframe["close"].shift(1)).abs()
            tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
            dataframe["atr"] = tr.rolling(self.atr_period).mean()

            # VPVR POC over rolling window
            def vpvr_poc(row_window):
                if len(row_window) < 10:
                    return np.nan
                closes = row_window.values
                hist, edges = np.histogram(closes, bins=self.vpvr_n_bins)
                poc_idx = np.argmax(hist)
                return (edges[poc_idx] + edges[poc_idx + 1]) / 2
            dataframe["vpvr_poc"] = dataframe["close"].rolling(self.vpvr_window_bars).apply(
                vpvr_poc, raw=False
            )
            dataframe["vpvr_distance_atr"] = (
                (dataframe["close"] - dataframe["vpvr_poc"]) / dataframe["atr"]
            ).abs()

            # Funding EMA proxy (use funding if available)
            if "funding_rate" in dataframe.columns:
                fr = dataframe["funding_rate"].fillna(method="ffill")
                dataframe["funding_8h_ema"] = fr.ewm(span=self.funding_8h_ema_window, adjust=False).mean()
            else:
                # No funding data → default to allowing all
                dataframe["funding_8h_ema"] = 0.0

        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        """Entry: short BTC / long SOL when z > threshold AND price near VPVR POC."""
        if metadata["pair"] != "BTCUSDT/SOLUSDT":
            return dataframe

        dataframe.loc[
            (
                (dataframe["zscore"] > self.zscore_entry_threshold) &
                (dataframe["vpvr_distance_atr"] < self.vpvr_proximity_atr_k) &
                (dataframe["funding_8h_ema"].abs() < self.funding_filter_threshold) &
                (dataframe["volume"] > 0)
            ),
            "enter_long",
        ] = 0  # BTC long, SOL short logic is pair-level; freqtrade backtest doesn't natively
               # do pair trading. We enter long to represent the pair position.

        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        """Exit: z returns to 0.5 OR regime breaks."""
        if metadata["pair"] != "BTCUSDT/SOLUSDT":
            return dataframe

        dataframe.loc[
            (
                (dataframe["zscore"] < self.zscore_exit_threshold) |
                (dataframe["zscore"] > self.regime_switch_zscore_threshold)
            ),
            "exit_long",
        ] = 1
        return dataframe
