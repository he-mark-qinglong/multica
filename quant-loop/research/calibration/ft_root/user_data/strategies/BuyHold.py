"""Calibration BuyHold strategy.

Enters long on the very first bar and never exits via signal/ROI/stop.
freqtrade auto-closes the position at the end of the backtest, which
for buy-and-hold is exactly the desired behaviour (entry fee + exit fee
at start and end). Used to calibrate the freqtrade framework against
the in-house buy-and-hold baseline.
"""
import numpy as np
import pandas as pd
from pandas import DataFrame
from freqtrade.strategy import IStrategy


class BuyHold(IStrategy):
    INTERFACE_VERSION = 3
    timeframe = "30m"
    can_short = False
    process_only_new_candles = False
    startup_candle_count = 1

    # Disable all exits except backtest-end forced liquidation.
    stoploss = -0.999
    trailing_stop = False
    minimal_roi = {"0": 10000.0}
    use_exit_signal = False
    exit_profit_only = False

    # Re-entering would break buy-hold; cap at 1 and never issue exit.
    max_open_trades = 1
    position_adjustment_enable = False

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        # Enter long on the first bar only (volume guard honoured).
        dataframe["enter_long"] = 0
        dataframe.loc[dataframe.index[0], "enter_long"] = 1
        dataframe["enter_tag"] = dataframe["enter_long"].apply(
            lambda x: "buyhold_entry" if x == 1 else ""
        )
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["exit_long"] = 0
        return dataframe
