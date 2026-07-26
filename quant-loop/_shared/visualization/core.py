"""Standard strategy visualization bundle.

Mandatory outputs for every strategy verdict:
  - equity_curve.png
  - trade_history_long.png / trade_history_short.png
  - trade_diagnostic.png
  - returns_heatmap.png
  - indicator_overlay.png (when indicator_df provided)
"""
from __future__ import annotations

import warnings
from pathlib import Path
from typing import Optional

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.patches import Rectangle

warnings.filterwarnings("ignore", category=FutureWarning)


class StrategyVisualizer:
    """Generate human-reviewable strategy artifacts."""

    def __init__(
        self,
        ohlc_df: pd.DataFrame,
        trades_df: pd.DataFrame,
        equity_df: pd.DataFrame,
        output_dir: str | Path,
        indicator_df: Optional[pd.DataFrame] = None,
        vpvr_df: Optional[pd.DataFrame] = None,
        symbol: str = "SYMBOL",
        cost_bps_rt: float = 0.0,
    ):
        """
        Parameters
        ----------
        ohlc_df : pd.DataFrame
            Must have columns open/high/low/close/volume with DatetimeIndex.
        trades_df : pd.DataFrame
            Must have columns: entry_time, exit_time, side ('long'/'short'),
            entry_price, exit_price, pnl_bps, size.
        equity_df : pd.DataFrame
            Must have columns: nav (or equity), with DatetimeIndex.
        output_dir : path
        indicator_df : pd.DataFrame, optional
            Additional indicator series to overlay on price chart.
        vpvr_df : pd.DataFrame, optional
            Volume profile with columns: price, volume.
        symbol : str
        cost_bps_rt : float
            Round-trip cost in bps for annotation.
        """
        self.ohlc = ohlc_df.copy()
        self.trades = trades_df.copy()
        self.equity = equity_df.copy()
        self.indicator = indicator_df.copy() if indicator_df is not None else None
        self.vpvr = vpvr_df.copy() if vpvr_df is not None else None
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.symbol = symbol
        self.cost_bps_rt = cost_bps_rt

        # Normalize column names
        for df, lower in [(self.ohlc, True), (self.equity, True)]:
            if lower:
                df.columns = [c.lower() for c in df.columns]
        self.trades.columns = [c.lower() for c in self.trades.columns]

        # Ensure DatetimeIndex
        for df_name, df in [("ohlc", self.ohlc), ("equity", self.equity)]:
            if not isinstance(df.index, pd.DatetimeIndex):
                raise ValueError(f"{df_name}_df must have DatetimeIndex")

        if "nav" not in self.equity.columns and "equity" in self.equity.columns:
            self.equity["nav"] = self.equity["equity"]
        if "nav" not in self.equity.columns:
            raise ValueError("equity_df must have 'nav' or 'equity' column")

        # Side normalization
        if "side" in self.trades.columns:
            self.trades["side"] = self.trades["side"].str.lower()

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #
    def generate_all(self, max_trade_samples: int = 150) -> dict[str, Path]:
        """Generate the full mandatory bundle."""
        paths = {}
        paths["equity_curve"] = self.plot_equity_curve()
        for side in ("long", "short"):
            paths[f"trade_history_{side}"] = self.plot_trade_history(side=side)
        paths["trade_diagnostic"] = self.plot_trade_diagnostic()
        paths["returns_heatmap"] = self.plot_returns_heatmap()
        if self.vpvr is not None:
            paths["vpvr_overlay"] = self.plot_vpvr_overlay()
        if self.indicator is not None:
            paths["indicator_overlay"] = self.plot_indicator_overlay()
        return paths

    def plot_equity_curve(self, figsize: tuple[int, int] = (14, 10)) -> Path:
        """Plot combined + per-side equity curves with drawdown bands."""
        fig, axes = plt.subplots(3, 1, figsize=figsize, sharex=True)
        fig.suptitle(f"{self.symbol} — Equity Curves (cost {self.cost_bps_rt}bps RT)", fontsize=14)

        sides = [("combined", self.equity["nav"]), ("long", None), ("short", None)]
        for ax, (label, _) in zip(axes, sides):
            if label == "combined":
                nav = self.equity["nav"]
            else:
                nav = self._side_equity(label)
            self._draw_equity_subplot(ax, nav, label)

        plt.tight_layout(rect=[0, 0, 1, 0.96])
        out = self.output_dir / "equity_curve.png"
        fig.savefig(out, dpi=150, bbox_inches="tight")
        plt.close(fig)
        return out

    def plot_trade_history(
        self,
        side: str = "long",
        n_windows: int = 6,
        trades_per_window: int = 5,
        window_bars: int = 120,
        figsize: tuple[int, int] = (20, 12),
    ) -> Path:
        """Plot representative trades in local K-line windows.

        Each subplot shows a short time slice (e.g. 120 1m bars) with candles,
        entry/exit markers, and a side-panel VPVR profile for that slice.
        """
        side = side.lower()
        trades = self.trades[self.trades["side"] == side].copy()
        if trades.empty:
            out = self.output_dir / f"trade_history_{side}.png"
            fig, ax = plt.subplots(figsize=(8, 4))
            ax.text(0.5, 0.5, f"No {side} trades", ha="center", va="center")
            fig.savefig(out, dpi=150)
            plt.close(fig)
            return out

        trades = trades.sort_values("entry_time").reset_index(drop=True)
        total = len(trades)

        # Pick n_windows evenly spaced seeds, then expand each seed to include
        # nearby trades so we get a local story rather than isolated dots.
        if total <= n_windows * trades_per_window:
            windows = [trades]
        else:
            seeds = np.linspace(0, total - 1, n_windows, dtype=int)
            half = trades_per_window // 2
            windows = []
            for s in seeds:
                lo = max(0, s - half)
                hi = min(total, lo + trades_per_window)
                lo = max(0, hi - trades_per_window)
                windows.append(trades.iloc[lo:hi])

        n_cols = 3
        n_rows = int(np.ceil(len(windows) / n_cols))
        fig = plt.figure(figsize=figsize)
        fig.suptitle(
            f"{self.symbol} {side.upper()} — local trade windows (sampled {sum(len(w) for w in windows)} of {total})",
            fontsize=14,
        )

        outer_grid = fig.add_gridspec(n_rows, n_cols, hspace=0.35, wspace=0.25)

        for idx, win_trades in enumerate(windows):
            row = idx // n_cols
            col = idx % n_cols
            inner = outer_grid[row, col].subgridspec(1, 2, width_ratios=[4, 1], wspace=0.05)
            ax_price = fig.add_subplot(inner[0])
            ax_vpvr = fig.add_subplot(inner[1], sharey=ax_price)

            start = win_trades["entry_time"].min() - pd.Timedelta(minutes=window_bars // 2)
            end = win_trades["exit_time"].max() + pd.Timedelta(minutes=window_bars // 2)
            ohlc = self.ohlc.loc[start:end]

            self._draw_candles(ax_price, ohlc)
            for _, t in win_trades.iterrows():
                self._draw_trade_arrow(ax_price, t, side)

            # Local VPVR for this window
            self._draw_local_vpvr(ax_vpvr, ohlc, win_trades, side)

            ax_price.set_ylabel("Price")
            ax_price.set_xlabel("Time")
            ax_price.set_title(f"{start.strftime('%Y-%m-%d %H:%M')} — {len(win_trades)} trades")
            ax_price.grid(True, alpha=0.3)
            ax_price.tick_params(axis="x", rotation=15)

            ax_vpvr.set_xlabel("Volume")
            ax_vpvr.tick_params(labelleft=False)
            ax_vpvr.grid(True, alpha=0.3)

        plt.tight_layout(rect=[0, 0, 1, 0.96])
        out = self.output_dir / f"trade_history_{side}.png"
        fig.savefig(out, dpi=150, bbox_inches="tight")
        plt.close(fig)
        return out

    def plot_trade_diagnostic(self, figsize: tuple[int, int] = (16, 12)) -> Path:
        """Scatter / histogram diagnostics of trade population."""
        if self.trades.empty:
            out = self.output_dir / "trade_diagnostic.png"
            fig, ax = plt.subplots(figsize=(8, 4))
            ax.text(0.5, 0.5, "No trades", ha="center", va="center")
            fig.savefig(out, dpi=150)
            plt.close(fig)
            return out

        df = self.trades.copy()
        df["duration"] = (df["exit_time"] - df["entry_time"]).dt.total_seconds() / 60.0
        df["hour"] = df["entry_time"].dt.hour
        df["dow"] = df["entry_time"].dt.day_name()

        fig, axes = plt.subplots(2, 2, figsize=figsize)
        fig.suptitle(f"{self.symbol} — Trade Diagnostic", fontsize=14)

        # 1. PnL vs duration
        ax = axes[0, 0]
        for side, color in [("long", "green"), ("short", "red")]:
            sub = df[df["side"] == side]
            if not sub.empty:
                ax.scatter(sub["duration"], sub["pnl_bps"], alpha=0.5, c=color, label=side, s=20)
        ax.axhline(0, color="black", linewidth=0.8)
        ax.set_xlabel("Duration (minutes)")
        ax.set_ylabel("PnL (bps)")
        ax.set_title("PnL vs Hold Time")
        ax.legend()
        ax.grid(True, alpha=0.3)

        # 2. PnL distribution
        ax = axes[0, 1]
        for side, color in [("long", "green"), ("short", "red")]:
            sub = df[df["side"] == side]
            if not sub.empty:
                ax.hist(sub["pnl_bps"], bins=50, alpha=0.5, color=color, label=side)
        ax.axvline(0, color="black", linewidth=0.8)
        ax.set_xlabel("PnL (bps)")
        ax.set_ylabel("Count")
        ax.set_title("PnL Distribution")
        ax.legend()
        ax.grid(True, alpha=0.3)

        # 3. PnL by entry hour
        ax = axes[1, 0]
        hour_pnl = df.groupby(["hour", "side"])["pnl_bps"].mean().unstack()
        hour_pnl.plot(kind="bar", ax=ax, color=["green", "red"], alpha=0.7)
        ax.axhline(0, color="black", linewidth=0.8)
        ax.set_xlabel("Hour of Day (UTC)")
        ax.set_ylabel("Mean PnL (bps)")
        ax.set_title("Mean PnL by Entry Hour")
        ax.grid(True, alpha=0.3)

        # 4. Top 10 contributors / detractors
        ax = axes[1, 1]
        top = df.nlargest(10, "pnl_bps")
        bottom = df.nsmallest(10, "pnl_bps")
        comb = pd.concat([bottom, top]).sort_values("pnl_bps")
        colors = ["red" if x < 0 else "green" for x in comb["pnl_bps"]]
        ax.barh(range(len(comb)), comb["pnl_bps"], color=colors, alpha=0.7)
        ax.set_yticks(range(len(comb)))
        ax.set_yticklabels([f"{r.side[:1].upper()} {r.entry_time.strftime('%m-%d %H:%M')}" for _, r in comb.iterrows()], fontsize=7)
        ax.set_xlabel("PnL (bps)")
        ax.set_title("Top 10 Contributors / Detractors")
        ax.grid(True, alpha=0.3)

        plt.tight_layout(rect=[0, 0, 1, 0.96])
        out = self.output_dir / "trade_diagnostic.png"
        fig.savefig(out, dpi=150, bbox_inches="tight")
        plt.close(fig)
        return out

    def plot_returns_heatmap(self, figsize: tuple[int, int] = (14, 6)) -> Path:
        """Monthly return heatmap by symbol."""
        # Build daily returns from equity NAV
        nav = self.equity["nav"].resample("D").last().dropna()
        if len(nav) < 2:
            out = self.output_dir / "returns_heatmap.png"
            fig, ax = plt.subplots(figsize=(8, 4))
            ax.text(0.5, 0.5, "Insufficient equity history", ha="center", va="center")
            fig.savefig(out, dpi=150)
            plt.close(fig)
            return out

        daily_ret = nav.pct_change().dropna()
        monthly = daily_ret.resample("ME").apply(lambda x: (1 + x).prod() - 1)
        df = pd.DataFrame({"ret": monthly})
        df["year"] = df.index.year
        df["month"] = df.index.month
        pivot = df.pivot_table(values="ret", index="year", columns="month", aggfunc="sum")
        pivot = pivot.reindex(columns=range(1, 13))

        fig, ax = plt.subplots(figsize=figsize)
        cmap = plt.cm.RdYlGn
        im = ax.imshow(pivot.values, cmap=cmap, aspect="auto", vmin=-0.15, vmax=0.15)
        ax.set_xticks(range(12))
        ax.set_xticklabels(["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"])
        ax.set_yticks(range(len(pivot.index)))
        ax.set_yticklabels(pivot.index)
        ax.set_title(f"{self.symbol} — Monthly Returns Heatmap")

        # Annotate cells
        for i in range(len(pivot.index)):
            for j in range(12):
                val = pivot.iloc[i, j]
                if not np.isnan(val):
                    text = f"{val*100:+.1f}%"
                    color = "white" if abs(val) > 0.075 else "black"
                    ax.text(j, i, text, ha="center", va="center", color=color, fontsize=8)

        fig.colorbar(im, ax=ax, label="Monthly return")
        plt.tight_layout()
        out = self.output_dir / "returns_heatmap.png"
        fig.savefig(out, dpi=150, bbox_inches="tight")
        plt.close(fig)
        return out

    def plot_vpvr_overlay(self, figsize: tuple[int, int] = (12, 8)) -> Path:
        """VPVR volume profile with long/short entry clusters."""
        if self.vpvr is None:
            raise ValueError("vpvr_df not provided")

        fig, ax = plt.subplots(figsize=figsize)
        vpvr = self.vpvr.copy()
        ax.barh(vpvr["price"], vpvr["volume"], height=vpvr["price"].diff().median() * 0.9, color="gray", alpha=0.5)

        for side, color in [("long", "green"), ("short", "red")]:
            sub = self.trades[self.trades["side"] == side]
            if not sub.empty:
                ax.scatter(sub["entry_price"], sub["entry_price"], c=color, label=f"{side} entry", s=15, alpha=0.4)
                ax.scatter(sub["exit_price"], sub["exit_price"], c=color, marker="x", label=f"{side} exit", s=15, alpha=0.4)

        ax.set_xlabel("Volume")
        ax.set_ylabel("Price")
        ax.set_title(f"{self.symbol} — VPVR Profile with Trade Clusters")
        ax.legend()
        ax.grid(True, alpha=0.3)
        plt.tight_layout()
        out = self.output_dir / "vpvr_overlay.png"
        fig.savefig(out, dpi=150, bbox_inches="tight")
        plt.close(fig)
        return out

    def plot_indicator_overlay(self, figsize: tuple[int, int] = (16, 10)) -> Path:
        """Price + indicator + trades."""
        if self.indicator is None:
            raise ValueError("indicator_df not provided")
        fig, axes = plt.subplots(2, 1, figsize=figsize, sharex=True)
        ax_price = axes[0]
        ax_ind = axes[1]

        self._draw_candles(ax_price, self.ohlc.iloc[-5000:])
        for side, color in [("long", "green"), ("short", "red")]:
            sub = self.trades[self.trades["side"] == side]
            if not sub.empty:
                ax_price.scatter(sub["entry_time"], sub["entry_price"], c=color, marker="^", s=30, label=f"{side} entry")

        for col in self.indicator.columns:
            ax_ind.plot(self.indicator.index[-5000:], self.indicator[col].iloc[-5000:], label=col, alpha=0.8)
        ax_ind.legend()
        ax_ind.grid(True, alpha=0.3)

        ax_price.set_title(f"{self.symbol} — Price + Signal Indicator")
        plt.tight_layout()
        out = self.output_dir / "indicator_overlay.png"
        fig.savefig(out, dpi=150, bbox_inches="tight")
        plt.close(fig)
        return out

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #
    def _side_equity(self, side: str) -> pd.Series:
        """Reconstruct equity curve for one side only."""
        trades = self.trades[self.trades["side"] == side].sort_values("exit_time")
        if trades.empty:
            return self.equity["nav"] * 0.0 + 1.0
        # Build cumulative product from trade returns; aggregate same exit_time.
        trades["ret"] = 1 + trades["pnl_bps"] / 10000.0
        daily_ret = trades.groupby("exit_time")["ret"].prod()
        nav = daily_ret.cumprod()
        nav = nav.reindex(self.equity.index, method="ffill").fillna(1.0)
        return nav

    def _draw_equity_subplot(self, ax: plt.Axes, nav: pd.Series, label: str):
        ax.plot(nav.index, nav / nav.iloc[0] - 1.0, linewidth=1.2, label=f"{label} return")
        # Drawdown band
        peak = nav.cummax()
        dd = (nav - peak) / peak
        ax.fill_between(nav.index, dd, 0, color="red", alpha=0.15, label="drawdown")
        # Max DD annotation
        max_dd_idx = dd.idxmin()
        max_dd = dd.min()
        ax.annotate(
            f"max dd {max_dd*100:.1f}%",
            xy=(max_dd_idx, max_dd),
            xytext=(10, 10),
            textcoords="offset points",
            fontsize=8,
            arrowprops=dict(arrowstyle="->", color="red"),
        )
        ax.set_ylabel("Cumulative return")
        ax.set_title(f"{label.capitalize()} Equity")
        ax.legend(loc="upper left")
        ax.grid(True, alpha=0.3)
        ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda y, _: f"{y*100:.0f}%"))

    def _draw_candles(self, ax: plt.Axes, ohlc: pd.DataFrame):
        """Draw OHLC candles using matplotlib patches."""
        width = pd.Timedelta(minutes=1) * 0.7
        for ts, row in ohlc.iterrows():
            open_, high, low, close = row["open"], row["high"], row["low"], row["close"]
            color = "green" if close >= open_ else "red"
            ax.add_patch(Rectangle((ts - width / 2, min(open_, close)), width, abs(close - open_), color=color, alpha=0.7))
            ax.plot([ts, ts], [low, high], color="black", linewidth=0.5)

    def _draw_trade_arrow(self, ax: plt.Axes, trade: pd.Series, side: str):
        entry_time = trade["entry_time"]
        exit_time = trade["exit_time"]
        entry_price = trade["entry_price"]
        exit_price = trade["exit_price"]
        pnl = trade["pnl_bps"]

        entry_marker = "^" if side == "long" else "v"
        exit_marker = "o" if pnl >= 0 else "x"
        entry_color = "green" if side == "long" else "red"
        exit_color = entry_color if pnl >= 0 else "black"

        ax.scatter(entry_time, entry_price, marker=entry_marker, c=entry_color, s=80, zorder=5, edgecolors="black", linewidths=0.5)
        ax.scatter(exit_time, exit_price, marker=exit_marker, c=exit_color, s=80, zorder=5, edgecolors="black", linewidths=0.5)
        ax.plot([entry_time, exit_time], [entry_price, exit_price], color=entry_color, alpha=0.3, linewidth=1)

    def _draw_local_vpvr(self, ax: plt.Axes, ohlc: pd.DataFrame, trades: pd.DataFrame, side: str):
        """Draw a local volume-at-price profile for the window."""
        if ohlc.empty:
            return
        lo = float(ohlc["low"].min())
        hi = float(ohlc["high"].max())
        if hi <= lo:
            return
        mid = 0.5 * (lo + hi)
        bucket_abs = max((hi - lo) / 40, mid * 0.0005)  # ~5bp buckets or 40 slices
        edges = np.arange(lo, hi + bucket_abs, bucket_abs)
        centers = 0.5 * (edges[:-1] + edges[1:])
        vol = np.zeros(len(centers))

        lows = ohlc["low"].to_numpy(float)
        highs = ohlc["high"].to_numpy(float)
        volumes = ohlc["quote_volume"].to_numpy(float)

        for i in range(len(centers)):
            mask = (lows < edges[i + 1]) & (highs > edges[i])
            if not mask.any():
                continue
            # Uniform intra-bar distribution approximation.
            bar_span = np.clip(highs[mask] - lows[mask], bucket_abs * 0.1, None)
            overlap = np.minimum(highs[mask], edges[i + 1]) - np.maximum(lows[mask], edges[i])
            weights = overlap / bar_span
            vol[i] = (volumes[mask] * weights).sum()

        ax.barh(centers, vol, height=bucket_abs * 0.9, color="gray", alpha=0.5)

        # Mark trade entries/exits on the profile.
        color = "green" if side == "long" else "red"
        for _, t in trades.iterrows():
            ax.scatter([vol.max() * 0.05], [t["entry_price"]], marker="^" if side == "long" else "v", c=color, s=50, zorder=5)
            exit_color = color if t["pnl_bps"] >= 0 else "black"
            ax.scatter([vol.max() * 0.05], [t["exit_price"]], marker="o" if t["pnl_bps"] >= 0 else "x", c=exit_color, s=50, zorder=5)
