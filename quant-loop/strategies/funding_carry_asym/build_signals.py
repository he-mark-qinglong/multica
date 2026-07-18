"""Signal builder for funding_carry_asym (SMA-34793 prototype).

Public API
----------
``compute_signal(close, funding, levels, ...)``
    The pure-function signal generator. Takes the three upstream
    inputs (price, funding, VPVR levels) and emits a per-bar
    long/flat signal. No I/O, no module-level state, deterministic.

``build_signals(df, params)``
    The ``build_signals``-shaped wrapper the strategy harness
    consumes. Accepts an OHLCV DataFrame (with a DatetimeIndex and
    a `funding` column) plus the prototype's `params` block, runs
    the VPVR level detector (SMA-34790) on the rolling trailing
    window with a ``shift(1)`` to lock in no-look-ahead, then
    delegates to ``compute_signal``.

Why two layers
--------------
``compute_signal`` is the *testable core*: pure, easy to feed
synthetic fixtures into, and it's what the unit tests cover.
``build_signals`` is the *integration wrapper* that an
``strategy.py`` or ``run_backtest.py`` would call — it deals with
data plumbing (Df alignment, ATR roll, VPVR snapshot grid, level
shift) and produces a DataFrame indexed exactly like ``df``.

Spec reference
--------------
See ``SPEC.md`` in this directory. Done criteria from SMA-34793:
  - funding-just-above-threshold case fires long
  - funding-just-below-threshold case does not fire long
  - price-at-VPVR-support case fires long
  - price-far-from-support case does not fire long
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import List, Optional

import numpy as np
import pandas as pd

# Make the indicator package importable without touching sys.path
# from outside this directory.
_INDICATORS_DIR = Path("/home/smark/multica/quant-loop/strategies/_indicators")
if str(_INDICATORS_DIR) not in sys.path:
    sys.path.insert(0, str(_INDICATORS_DIR))

from vpvr_levels import VpvrLevel, detect_vpvr_levels  # noqa: E402


# ---------------------------------------------------------------------------
# Defaults — single source of truth so callers don't disagree.
# ---------------------------------------------------------------------------
DEFAULT_FUNDING_THRESHOLD: float = 0.0003     # 0.03% per 8h event = 3 bps
DEFAULT_SUPPORT_KIND: str = "HVN"            # high-volume node = absorption support
DEFAULT_PROXIMITY_ATR: float = 1.0           # bars within 1.0 ATR of the level center
DEFAULT_ATR_PERIOD: int = 14
DEFAULT_VPVR_WINDOW_BARS: int = 180          # 30 days @ 4h
DEFAULT_VPVR_SNAPSHOT_EVERY_BARS: int = 6    # every 6 × 4h ≈ daily snapshot
DEFAULT_VPVR_BINS: int = 24
DEFAULT_VPVR_HVN_QUANTILE: float = 0.85
DEFAULT_VPVR_LVN_QUANTILE: float = 0.15
DEFAULT_VPVR_NUM_HVN: int = 3
DEFAULT_VPVR_NUM_LVN: int = 3


# ---------------------------------------------------------------------------
# Pure-function core (the testable surface).
# ---------------------------------------------------------------------------
def _atr_from_close(close: pd.Series, period: int) -> pd.Series:
    """ATR synthesized from a close series only (no high/low).

    Used by ``compute_signal`` when the caller hands us close but
    no high/low. Falls back to ``close.diff().abs()`` as the "true
    range" proxy. This is a lower-bound estimate; callers that have
    real OHLC should pre-compute the standard ATR via
    ``_atr_ohlcv``.
    """
    prev = close.shift(1)
    tr = pd.concat([(close - prev).abs(),
                    (close - close).abs(),  # no-op, kept for shape uniformity
                    (close - prev).abs()], axis=1).max(axis=1)
    return tr.rolling(period, min_periods=period).mean().rename("atr")


def _atr_ohlcv(df: pd.DataFrame, period: int) -> pd.Series:
    """Standard rolling-mean ATR with ``close.shift(1)`` so today's
    range cannot leak into today's ATR. Matches the cycle-46
    convention used in the wider catalog.
    """
    h = df["high"].astype(np.float64)
    l = df["low"].astype(np.float64)
    c = df["close"].astype(np.float64).shift(1)
    tr = pd.concat([h - l, (h - c).abs(), (l - c).abs()], axis=1).max(axis=1)
    return tr.rolling(period, min_periods=period).mean().rename("atr")


def _resolve_levels_at_bar(
    levels: List[VpvrLevel],
    px: float,
    kind: str,
) -> Optional[VpvrLevel]:
    """Return the single support level of the given kind closest to px
    by absolute price distance, or None when no such level exists.

    HVN-by-volume-rank is the desired support (largest volume = most
    price-magnetic). LVN-by-rank is the strongest-low-volume (lowest
    volume = the truest "slip-through" gap). We pick the closest in
    price space; volume rank only resolves ties via insertion order
    (so the first-listed HVN near the price wins).
    """
    candidates = [lv for lv in levels if lv.kind == kind]
    if not candidates:
        return None
    return min(candidates, key=lambda lv: abs(lv.price_center - px))


def compute_signal(
    close: pd.Series,
    funding: pd.Series,
    levels: List[VpvrLevel],
    *,
    funding_threshold: float = DEFAULT_FUNDING_THRESHOLD,
    support_kind: str = DEFAULT_SUPPORT_KIND,
    proximity_atr: float = DEFAULT_PROXIMITY_ATR,
    atr: Optional[pd.Series] = None,
    atr_period: int = DEFAULT_ATR_PERIOD,
    funding_percentile: Optional[pd.Series] = None,
) -> pd.DataFrame:
    """Pure-function funding-carry-asym signal generator.

    Parameters
    ----------
    close
        Per-bar close prices, indexed by ``ts`` (any index that aligns
        with ``funding``).
    funding
        Funding rate per 8h event, same index as ``close``. The
        convention is that ``funding[t]`` is the rate paid at the
        most recent funding event strictly before bar ``t``'s open —
        callers are responsible for the ffill + one-step shift that
        enforces no look-ahead. The ``build_signals`` wrapper below
        does this for them.
    levels
        Output of ``vpvr_levels.detect_vpvr_levels`` for the trailing
        window. The signal uses every level whose ``kind`` matches
        ``support_kind``.
    funding_threshold
        Minimum funding rate (raw, per 8h event) required to fire
        long. Default ``0.0003`` = ``0.03%`` per 8h event. Ignored
        when ``funding_percentile`` is provided.
    support_kind
        ``"HVN"`` or ``"LVN"``. Default ``"HVN"`` — high-volume
        absorption zones are the natural reading of "support" in
        the funding-carry-asym spec.
    proximity_atr
        Maximum allowed distance from the level center, in ATR
        multiples. Default ``1.0``.
    atr
        Pre-computed ATR series aligned to ``close``. When ``None``,
        the function falls back to a ``close.diff()``-only ATR with
        period ``atr_period``. Pass the standard OHLCV ATR when
        available (the ``build_signals`` wrapper does so).
    atr_period
        Used only when ``atr`` is None.
    funding_percentile
        Optional rolling percentile series (same index as ``funding``).
        When provided, fires on ``fd > funding_percentile[t]`` instead
        of ``fd > funding_threshold``. Used by SMA-34928 to expose the
        lower-tail edge on BTC 15m where absolute funding never
        crosses 0.0003 in the recent 30d window.

    Returns
    -------
    pd.DataFrame
        Indexed identically to ``close``. Columns:
        - ``signal`` (int64, {-1, 0, +1}; this prototype is +1/0)
        - ``funding`` (float, the funding rate used at the bar)
        - ``funding_above_threshold`` (bool)
        - ``support_level_price`` (float, NaN if no match)
        - ``support_level_kind`` (object, "HVN"/"LVN"/"")
        - ``support_distance_atr`` (float, NaN if no match)
        - ``near_support`` (bool)
        - ``atr`` (float)

    Notes
    -----
    This function is deterministic and free of side effects; the
    unit tests in ``tests/test_build_signals.py`` cover the four
    SMA-34793 cases plus the no-look-ahead invariant.
    """
    if not isinstance(close, pd.Series):
        raise TypeError(f"close must be a pd.Series, got {type(close).__name__}")
    if not isinstance(funding, pd.Series):
        raise TypeError(f"funding must be a pd.Series, got {type(funding).__name__}")
    if support_kind not in ("HVN", "LVN"):
        raise ValueError(f"support_kind must be 'HVN' or 'LVN', got {support_kind!r}")
    if proximity_atr <= 0:
        raise ValueError(f"proximity_atr must be > 0, got {proximity_atr!r}")
    if funding_threshold <= 0:
        raise ValueError(f"funding_threshold must be > 0, got {funding_threshold!r}")

    # Align funding to the close index without implicit look-ahead —
    # the caller is required to have already done the ffill+shift.
    # We reindex here defensively, so even an unshifted funding will
    # not get a free future peep: rows whose funding index sits past
    # the close index simply become NaN.
    funding_aligned = funding.reindex(close.index)
    funding_filled = funding_aligned.astype(np.float64)

    if atr is None:
        atr_series = _atr_from_close(close.astype(np.float64), atr_period)
    else:
        atr_series = atr.reindex(close.index).astype(np.float64)

    if funding_percentile is not None:
        pct_aligned = funding_percentile.reindex(close.index).astype(np.float64)
        pct_arr = pct_aligned.values
    else:
        pct_arr = None

    close_arr = close.astype(np.float64).values
    funding_arr = funding_filled.values
    atr_arr = atr_series.values
    n = len(close)

    signal = np.zeros(n, dtype=np.int64)
    funding_ok = np.zeros(n, dtype=bool)
    near_support = np.zeros(n, dtype=bool)
    support_px = np.full(n, np.nan, dtype=np.float64)
    support_kind_arr = np.array([""] * n, dtype=object)
    support_dist_atr = np.full(n, np.nan, dtype=np.float64)

    # For every bar, locate the closest support-kind level of `levels`
    # by absolute price distance. Levels are a single set computed
    # externally on prior data (caller's responsibility); this loop
    # is O(N_levels) per bar but with N_levels small (≤5 typical) and
    # using Python-level min is fast enough for 30d × N bars.
    for i in range(n):
        px = float(close_arr[i])
        fd = float(funding_arr[i]) if np.isfinite(funding_arr[i]) else 0.0
        at = float(atr_arr[i]) if np.isfinite(atr_arr[i]) else 0.0

        if pct_arr is not None and np.isfinite(pct_arr[i]):
            funding_ok[i] = fd > float(pct_arr[i])
        else:
            funding_ok[i] = fd > funding_threshold

        if at > 0 and np.isfinite(px):
            chosen = _resolve_levels_at_bar(levels, px, support_kind)
            if chosen is not None:
                dist = abs(px - chosen.price_center)
                dist_atr = dist / at
                support_dist_atr[i] = dist_atr
                support_px[i] = chosen.price_center
                support_kind_arr[i] = chosen.kind
                if dist_atr <= proximity_atr:
                    near_support[i] = True

        if funding_ok[i] and near_support[i]:
            signal[i] = 1

    idx = close.index
    return pd.DataFrame(
        {
            "signal": pd.Series(signal, index=idx, dtype=np.int64),
            "funding": pd.Series(funding_arr, index=idx, dtype=np.float64),
            "funding_above_threshold": pd.Series(funding_ok, index=idx, dtype=bool),
            "support_level_price": pd.Series(support_px, index=idx, dtype=np.float64),
            "support_level_kind": pd.Series(support_kind_arr, index=idx, dtype=object),
            "support_distance_atr": pd.Series(support_dist_atr, index=idx, dtype=np.float64),
            "near_support": pd.Series(near_support, index=idx, dtype=bool),
            "atr": pd.Series(atr_arr, index=idx, dtype=np.float64),
        }
    )


# ---------------------------------------------------------------------------
# build_signals-style wrapper for the strategy harness.
# ---------------------------------------------------------------------------
def _vpvr_snapshot_levels(
    high: pd.Series,
    low: pd.Series,
    volume: pd.Series,
    snapshot_idx: pd.DatetimeIndex,
    window: int,
    bins: int,
    hvn_quantile: float,
    lvn_quantile: float,
    num_hvn: int,
    num_lvn: int,
) -> pd.DataFrame:
    """Run ``detect_vpvr_levels`` on each snapshot bar's trailing
    window and pivot the result into a per-snapshot frame indexed
    by snapshot ts. Mirrors ``loid_vpvr_confluence_20260717``.

    The snapshot is computed **inclusive of the snapshot bar**
    itself, so when the caller wants the level used at bar ``t``
    they take ``levels.shift(1).reindex(close.index).ffill()`` —
    which is what ``build_signals`` does below.
    """
    pos = {ts: i for i, ts in enumerate(high.index)}
    out = {
        "hvn_top": np.full(len(snapshot_idx), np.nan),
        "hvn_bot": np.full(len(snapshot_idx), np.nan),
        "hvn_mid": np.full(len(snapshot_idx), np.nan),
        "lvn_top": np.full(len(snapshot_idx), np.nan),
        "lvn_bot": np.full(len(snapshot_idx), np.nan),
        "lvn_mid": np.full(len(snapshot_idx), np.nan),
        "levels_per_bar": np.full(len(snapshot_idx), np.nan, dtype=object),
    }
    for k, ts in enumerate(snapshot_idx):
        end = pos[ts]
        start = max(0, end - window + 1)
        if end - start + 1 < max(20, window // 4):
            continue
        try:
            lv = detect_vpvr_levels(
                pd.DataFrame({"high": high.iloc[start: end + 1],
                              "low": low.iloc[start: end + 1],
                              "volume": volume.iloc[start: end + 1]}),
                num_bins=bins,
                hvn_quantile=hvn_quantile,
                lvn_quantile=lvn_quantile,
                num_hvn=num_hvn,
                num_lvn=num_lvn,
            )
        except (ValueError, ZeroDivisionError):
            continue
        out["levels_per_bar"][k] = lv
        hvns = [x for x in lv if x.kind == "HVN"]
        lvns = [x for x in lv if x.kind == "LVN"]
        if hvns:
            top = max(x.price_high for x in hvns)
            bot = min(x.price_low for x in hvns)
            out["hvn_top"][k] = top
            out["hvn_bot"][k] = bot
            out["hvn_mid"][k] = 0.5 * (top + bot)
        if lvns:
            top = max(x.price_high for x in lvns)
            bot = min(x.price_low for x in lvns)
            out["lvn_top"][k] = top
            out["lvn_bot"][k] = bot
            out["lvn_mid"][k] = 0.5 * (top + bot)
    return pd.DataFrame(out, index=snapshot_idx)


def build_signals(df: pd.DataFrame, params: dict) -> pd.DataFrame:
    """``build_signals``-shape wrapper. See module docstring.

    Required ``df`` columns:
        ``open``, ``high``, ``low``, ``close``, ``volume``,
        ``funding`` (per-bar funding rate, already ffilled to the
        bar index; this wrapper does the no-look-ahead shift itself).

    Required ``params`` keys (with defaults from the module-level
    constants when omitted):
        ``funding_threshold``     (default 0.0003; ignored if
                                   ``funding_percentile_q`` is set)
        ``funding_percentile_q``  (optional, e.g. 80.0 = top 20%;
                                   computed on the funding-event
                                   cadence, NOT per bar)
        ``funding_lookback_events`` (default 90; ~30d @ 8h events)
        ``support_kind``          (default "HVN")
        ``proximity_atr``         (default 1.0)
        ``atr_period``            (default 14)
        ``vpvr_window_bars``      (default 180)
        ``vpvr_snapshot_every_bars`` (default 6)
        ``vpvr_bins``             (default 24)
        ``vpvr_hvn_quantile``     (default 0.85)
        ``vpvr_lvn_quantile``     (default 0.15)
        ``vpvr_num_hvn``          (default 3)
        ``vpvr_num_lvn``          (default 3)
    """
    df = df.copy()
    if not isinstance(df.index, pd.DatetimeIndex):
        if "ts" in df.columns:
            df = df.set_index("ts")
        elif "open_time" in df.columns:
            df = df.set_index("open_time")
        else:
            raise ValueError("df must have a DatetimeIndex or a ts/open_time column")
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    df = df.sort_index()

    if "funding" not in df.columns:
        raise ValueError(
            "df must include a 'funding' column (ffill of the 8h funding events "
            "onto the bar index). The harness is responsible for that merge."
        )

    close = df["close"].astype(np.float64)
    high = df["high"].astype(np.float64)
    low = df["low"].astype(np.float64)
    volume = df["volume"].astype(np.float64)
    funding_raw = df["funding"].astype(np.float64)

    # No-look-ahead: funding at bar t must have been paid strictly
    # before bar t's open. If the caller ffilled the funding events
    # to the bar index, the *first* bar that "sees" a funding event
    # at time f is the bar whose open_time > f; reverse that by
    # shifting the funding series back by one bar.
    funding = funding_raw.shift(1)

    # SMA-34928: optional percentile-based funding gate. When
    # `funding_percentile_q` is provided we compute a rolling
    # percentile on the funding-EVENT cadence (8h) using the last
    # `funding_lookback_events` events, then forward-fill that
    # percentile onto the bar index. The signal fires when
    # funding > rolling percentile, which adapts to whatever the
    # recent regime is rather than relying on a fixed absolute
    # threshold (the original 0.0003 never fires on the BTC 15m
    # 30d window where funding max is 0.0001).
    funding_percentile = None
    pct_q = params.get("funding_percentile_q")
    if pct_q is not None:
        q = float(pct_q)
        if not (0.0 < q < 100.0):
            raise ValueError(
                f"funding_percentile_q must be in (0, 100), got {q!r}"
            )
        lookback_n = int(params.get("funding_lookback_events", 90))
        # Use funding_raw (no shift) so the percentile reflects the
        # full event distribution available up to each event ts.
        # The per-bar funding[filling] is ffill-onto-bar of these
        # events; reindex the rolling percentile onto the bar index
        # with ffill so each bar sees the most recent event's pct.
        events = funding_raw.dropna()
        # Rolling percentile: at event ts e, take the prior N events
        # (inclusive of e itself? use strict < e to avoid peeking).
        # To enforce no-look-ahead, use shift(1) on the rolling pct.
        roll = events.shift(1).rolling(lookback_n, min_periods=max(20, lookback_n // 4))
        pct_at_event = roll.quantile(q / 100.0)
        # Reindex back onto the bar index, ffill (carries the latest
        # event's percentile forward until the next event).
        funding_percentile = pct_at_event.reindex(df.index, method="ffill")

    atr = _atr_ohlcv(df, int(params.get("atr_period", DEFAULT_ATR_PERIOD)))

    # Per-bar rolling VPVR on a snapshot grid. The level "used at
    # bar t" is the snapshot computed on bars [t-W, t] shifted by
    # 1, so it does not include bar t's own contribution.
    stride = max(1, int(params.get("vpvr_snapshot_every_bars",
                                   DEFAULT_VPVR_SNAPSHOT_EVERY_BARS)))
    snapshot_idx = df.index[::stride]
    if len(df.index) and df.index[-1] not in snapshot_idx:
        snapshot_idx = snapshot_idx.append(pd.DatetimeIndex([df.index[-1]]))

    snap = _vpvr_snapshot_levels(
        high, low, volume, snapshot_idx,
        window=int(params.get("vpvr_window_bars", DEFAULT_VPVR_WINDOW_BARS)),
        bins=int(params.get("vpvr_bins", DEFAULT_VPVR_BINS)),
        hvn_quantile=float(params.get("vpvr_hvn_quantile", DEFAULT_VPVR_HVN_QUANTILE)),
        lvn_quantile=float(params.get("vpvr_lvn_quantile", DEFAULT_VPVR_LVN_QUANTILE)),
        num_hvn=int(params.get("vpvr_num_hvn", DEFAULT_VPVR_NUM_HVN)),
        num_lvn=int(params.get("vpvr_num_lvn", DEFAULT_VPVR_NUM_LVN)),
    )
    snap_shifted = snap.shift(1)

    # Per-bar levels: the snapshot frame is widened to per-bar by
    # ffill on the shifted index. At a per-bar tick we re-materialise
    # the list of VpvrLevel objects from hvn_* / lvn_* columns; this
    # is conservative (we may copy the level through a price band
    # shift(1) later) but matches what a 4h snapshot's per-bar
    # caller would see in production.
    snap_per_bar = snap_shifted.reindex(df.index).ffill()

    def _levels_at_bar(bar_ts) -> List[VpvrLevel]:
        row = snap_per_bar.loc[bar_ts]
        out: List[VpvrLevel] = []
        hvn_top = row.get("hvn_top", np.nan)
        hvn_bot = row.get("hvn_bot", np.nan)
        hvn_mid = row.get("hvn_mid", np.nan)
        lvn_top = row.get("lvn_top", np.nan)
        lvn_bot = row.get("lvn_bot", np.nan)
        lvn_mid = row.get("lvn_mid", np.nan)
        if np.isfinite(hvn_top) and np.isfinite(hvn_bot):
            out.append(VpvrLevel(
                kind="HVN", price_low=float(hvn_bot), price_high=float(hvn_top),
                price_center=float(hvn_mid), volume=0.0, score=1.0,
            ))
        if np.isfinite(lvn_top) and np.isfinite(lvn_bot):
            out.append(VpvrLevel(
                kind="LVN", price_low=float(lvn_bot), price_high=float(lvn_top),
                price_center=float(lvn_mid), volume=0.0, score=1.0,
            ))
        return out

    # Vectorise the per-bar levels: most bars see the same snapshot
    # level (the snapshot grid is coarser than the bar cadence), so
    # we can group by ffill time and compute compute_signal on each
    # group's slice with the same levels. This avoids the O(N×L)
    # Python loop while preserving the spec's per-bar semantics.
    out_signal = pd.Series(0, index=df.index, dtype=np.int64)
    out_funding = pd.Series(np.nan, index=df.index, dtype=np.float64)
    out_funding_ok = pd.Series(False, index=df.index, dtype=bool)
    out_support_px = pd.Series(np.nan, index=df.index, dtype=np.float64)
    out_support_kind = pd.Series("", index=df.index, dtype=object)
    out_support_dist = pd.Series(np.nan, index=df.index, dtype=np.float64)
    out_near = pd.Series(False, index=df.index, dtype=bool)
    out_atr = atr.copy()

    # Group bars by snapshot ts (the shifted per-bar levels index).
    snapshot_groups = (
        snap_per_bar["hvn_mid"].groupby(snap_per_bar["hvn_mid"].index).groups
    )
    # Build levels per unique snapshot timestamp to share compute.
    unique_lv = {
        ts: _levels_at_bar(ts) for ts in snap_per_bar.index.unique()
    }

    for ts, group_idx in snapshot_groups.items():
        levels = unique_lv.get(ts, [])
        sub_close = close.loc[group_idx]
        sub_funding = funding.loc[group_idx]
        sub_atr = atr.loc[group_idx] if atr is not None else None
        sub_pct = funding_percentile.loc[group_idx] if funding_percentile is not None else None
        sig = compute_signal(
            sub_close, sub_funding, levels,
            funding_threshold=float(params.get("funding_threshold",
                                               DEFAULT_FUNDING_THRESHOLD)),
            support_kind=str(params.get("support_kind", DEFAULT_SUPPORT_KIND)),
            proximity_atr=float(params.get("proximity_atr",
                                           DEFAULT_PROXIMITY_ATR)),
            atr=sub_atr,
            funding_percentile=sub_pct,
        )
        out_signal.loc[group_idx] = sig["signal"].values
        out_funding.loc[group_idx] = sig["funding"].values
        out_funding_ok.loc[group_idx] = sig["funding_above_threshold"].values
        out_support_px.loc[group_idx] = sig["support_level_price"].values
        out_support_kind.loc[group_idx] = sig["support_level_kind"].values
        out_support_dist.loc[group_idx] = sig["support_distance_atr"].values
        out_near.loc[group_idx] = sig["near_support"].values
        if "atr" in sig.columns:
            out_atr.loc[group_idx] = sig["atr"].values

    return pd.DataFrame({
        "signal": out_signal,
        "funding": out_funding,
        "funding_above_threshold": out_funding_ok,
        "support_level_price": out_support_px,
        "support_level_kind": out_support_kind,
        "support_distance_atr": out_support_dist,
        "near_support": out_near,
        "atr": out_atr,
        # Diagnostic pass-through so the backtest harness can introspect.
        "hvn_top": snap_per_bar["hvn_top"],
        "hvn_bot": snap_per_bar["hvn_bot"],
        "lvn_top": snap_per_bar["lvn_top"],
        "lvn_bot": snap_per_bar["lvn_bot"],
    })


__all__ = [
    "DEFAULT_FUNDING_THRESHOLD",
    "DEFAULT_SUPPORT_KIND",
    "DEFAULT_PROXIMITY_ATR",
    "DEFAULT_ATR_PERIOD",
    "compute_signal",
    "build_signals",
]
