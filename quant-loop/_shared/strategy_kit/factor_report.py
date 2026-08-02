"""Factor validation reporter (metric A5 companion) — alphalens-style
report for the single-asset time-series factors of
:mod:`_shared.strategy_kit.factor_library`.

Classic alphalens is cross-sectional (many assets per date).  Library
factors here are time series on ONE asset, so this module implements the
time-series analogue: factor values are bucketed into quantiles *over
time* and forward returns are measured conditional on the quantile.

Causal discipline
-----------------
- The factor value at bar ``t`` is a feature known at ``t`` (library
  factors are causal by construction).
- The forward return at bar ``t`` over horizon ``h`` is
  ``close[t+h] / close[t] - 1``: future prices appear ONLY as the
  prediction target, never as a feature.
- NaN warmup tails (factor lookback) and NaN forward-return tails (last
  ``h`` bars per horizon) are dropped per horizon.

Components
----------
1. Quantile portfolio returns — mean forward return per time-quantile
   per horizon, plus a top-minus-bottom spread row.
2. IC by period — Spearman rank IC between factor_t and the h-bar
   forward return, per horizon (IC decay table), with chunked
   mean/std/ICIR for stability context.
3. Quantile turnover — per quantile q, P(bar t in q was in a different
   quantile at t-1 | bar t in q); plus the overall switch probability.
4. Factor rank autocorrelation — autocorrelation of the factor's
   cross-time percentage ranks at several lags.

All compute functions are pure (no I/O); only
:func:`generate_library_report` optionally writes a file.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from html import escape
from typing import Sequence

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

from _shared.strategy_kit.factor_library import compute_factor, list_factors

DEFAULT_PERIODS = (1, 5, 10)
DEFAULT_RANK_LAGS = (1, 5, 10)


@dataclass(frozen=True)
class FactorReport:
    """Validation output for one factor on one asset."""

    name: str
    direction: int                     # +1 long-high / -1 long-low
    n_obs: int                         # bars used after warmup dropna
    quantiles: int
    quantile_returns: pd.DataFrame     # rows Q1..Qq + 'top_minus_bottom', cols = horizons
    ic_by_period: pd.DataFrame         # rows = horizons, cols ic/mean_ic/std_ic/icir/n_obs
    quantile_turnover: pd.DataFrame    # rows Q1..Qq + 'overall', col 'turnover'
    rank_autocorrelation: pd.DataFrame  # rows = lags, col 'rank_autocorr'
    skipped: bool = False
    skip_reason: str = ""
    extra: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------

def _forward_returns(close: pd.Series, periods: Sequence[int]) -> dict[int, pd.Series]:
    """h-bar forward simple return at time t: close[t+h]/close[t] - 1.

    Indexing convention: the value at index t is earned between t and
    t+h — future prices are used only as the prediction target.
    """
    close = close.astype(float)
    return {h: close.shift(-h) / close - 1.0 for h in periods}


def _assign_quantiles(factor: pd.Series, quantiles: int) -> pd.Series:
    """Bucket factor values into time-quantiles (0 = lowest)."""
    q = pd.qcut(factor, quantiles, labels=False, duplicates="drop")
    return q.astype(float)


def _quantile_return_table(
    quantile: pd.Series,
    fwd: dict[int, pd.Series],
) -> pd.DataFrame:
    """Mean forward return per quantile x horizon + top-minus-bottom row."""
    table = {}
    for h, r in fwd.items():
        df = pd.DataFrame({"q": quantile, "r": r}).dropna()
        means = df.groupby("q")["r"].mean()
        table[h] = means
    out = pd.DataFrame(table)
    out.columns = [f"{h}" for h in fwd]
    spread = out.loc[out.index.max()] - out.loc[out.index.min()]
    out.loc["top_minus_bottom"] = spread
    return out


def _ic_decay_table(
    factor: pd.Series,
    fwd: dict[int, pd.Series],
    ic_chunks: int = 10,
) -> pd.DataFrame:
    """Spearman rank IC per horizon (IC decay table).

    Columns: overall IC on the full sample, plus mean/std/ICIR of
    per-chunk ICs (contiguous chunks) to show stability over time.
    """
    rows = {}
    for h, r in fwd.items():
        df = pd.DataFrame({"f": factor, "r": r}).dropna()
        n = len(df)
        if n < 10 or df["f"].nunique() < 2 or df["r"].nunique() < 2:
            rows[h] = {"ic": np.nan, "mean_ic": np.nan, "std_ic": np.nan,
                       "icir": np.nan, "n_obs": n}
            continue
        ic = float(spearmanr(df["f"], df["r"]).statistic)
        # contiguous chunks → distribution of ICs over time
        chunk_ics = []
        chunks = np.array_split(np.arange(n), min(ic_chunks, n))
        for idx in chunks:
            if len(idx) < 10:
                continue
            sub = df.iloc[idx]
            if sub["f"].nunique() < 2 or sub["r"].nunique() < 2:
                continue
            chunk_ics.append(float(spearmanr(sub["f"], sub["r"]).statistic))
        mean_ic = float(np.mean(chunk_ics)) if chunk_ics else np.nan
        std_ic = float(np.std(chunk_ics, ddof=1)) if len(chunk_ics) > 1 else np.nan
        icir = mean_ic / std_ic if std_ic and std_ic > 0 else np.nan
        rows[h] = {"ic": ic, "mean_ic": mean_ic, "std_ic": std_ic,
                   "icir": icir, "n_obs": n}
    out = pd.DataFrame(rows).T
    out.index.name = "horizon"
    return out


def _quantile_turnover(quantile: pd.Series) -> pd.DataFrame:
    """Time-series quantile turnover.

    Definition: for quantile q, turnover(q) = P(bar t is in q AND bar
    t-1 was in a different quantile | bar t is in q), computed over the
    dropna'd sample (consecutive valid bars).  'overall' = unconditional
    P(quantile changes between consecutive bars).  Values lie in [0, 1];
    a white-noise factor sits near 1 - 1/Q, a persistent factor near 0.
    """
    q = quantile.dropna()
    prev = q.shift(1)
    valid = prev.notna()
    switched = (q != prev) & valid
    rows = {}
    for v in sorted(q.unique()):
        mask = (q == v) & valid
        rows[f"Q{int(v) + 1}"] = float(switched[mask].mean()) if mask.any() else np.nan
    rows["overall"] = float(switched[valid].mean()) if valid.any() else np.nan
    return pd.DataFrame(rows, index=["turnover"]).T


def _rank_autocorrelation(
    factor: pd.Series, lags: Sequence[int],
) -> pd.DataFrame:
    """Autocorrelation of the factor's cross-time percentage ranks.

    Pearson correlation of pct-ranks at lag ℓ (equivalently the Spearman
    autocorrelation of the raw factor).  Persistent (slowly-varying)
    factors score near 1 at short lags, white noise near 0.
    """
    f = factor.dropna()
    ranks = f.rank(pct=True)
    rows = {}
    for lag in lags:
        if len(ranks) <= lag + 2:
            rows[lag] = np.nan
            continue
        rows[lag] = float(ranks.autocorr(lag=lag))
    out = pd.DataFrame(rows, index=["rank_autocorr"]).T
    out.index.name = "lag"
    return out


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def compute_factor_report(
    factor: pd.Series,
    close: pd.Series,
    name: str | None = None,
    direction: int = 1,
    quantiles: int = 5,
    periods: Sequence[int] = DEFAULT_PERIODS,
    rank_lags: Sequence[int] = DEFAULT_RANK_LAGS,
    ic_chunks: int = 10,
) -> FactorReport:
    """Compute the four validation components for one factor series.

    Parameters
    ----------
    factor : pd.Series
        Causal factor values indexed by bar timestamp.
    close : pd.Series
        Close prices on the same index (forward-return target source).
    name, direction : report metadata (direction from FactorSpec: +1
        long-high / -1 long-low; never used to flip signs silently).
    quantiles : number of time-quantile buckets.
    periods : forward-return horizons in bars.
    rank_lags : lags for the rank autocorrelation table.
    ic_chunks : number of contiguous chunks for IC stability stats.
    """
    factor = factor.astype(float).replace([np.inf, -np.inf], np.nan)
    close = close.astype(float)
    aligned = pd.concat([factor.rename("f"), close.rename("c")], axis=1).dropna()
    if len(aligned) < 20:
        raise ValueError(f"insufficient observations after warmup dropna: {len(aligned)}")
    f = aligned["f"]
    c = aligned["c"]
    if f.nunique() < 2:
        raise ValueError("factor is constant — no quantile structure")

    n_unique = f.nunique()
    q_eff = int(min(quantiles, n_unique))

    quantile = _assign_quantiles(f, q_eff)
    fwd = _forward_returns(c, periods)

    return FactorReport(
        name=name or (factor.name if factor.name else "factor"),
        direction=direction,
        n_obs=len(aligned),
        quantiles=q_eff,
        quantile_returns=_quantile_return_table(quantile, fwd),
        ic_by_period=_ic_decay_table(f, fwd, ic_chunks=ic_chunks),
        quantile_turnover=_quantile_turnover(quantile),
        rank_autocorrelation=_rank_autocorrelation(f, rank_lags),
    )


_CSS = """
<style>
body { font-family: -apple-system, Helvetica, Arial, sans-serif; margin: 2em;
       color: #222; }
h1 { font-size: 1.4em; } h2 { font-size: 1.15em; margin-top: 1.6em; }
h3 { font-size: 1.0em; margin-top: 1.2em; }
table { border-collapse: collapse; margin: 0.6em 0; font-size: 0.9em; }
th, td { border: 1px solid #ccc; padding: 4px 10px; text-align: right; }
th { background: #f2f2f2; }
.meta { color: #555; font-size: 0.9em; }
.skipped { color: #a00; }
</style>
"""


def _df_html(df: pd.DataFrame, float_fmt: str = "{:.6f}") -> str:
    return df.to_html(float_format=lambda v: float_fmt.format(v), border=0)


def report_to_html(report: FactorReport, title: str | None = None) -> str:
    """Render one :class:`FactorReport` as a self-contained HTML string.

    Contains the four component sections (acceptance headings):
    "Quantile Portfolio Returns", "IC by Period", "Quantile Turnover",
    "Factor Rank Autocorrelation".
    """
    title = title or f"Factor Report: {report.name}"
    if report.skipped:
        return (
            f"<h2>{escape(title)}</h2>"
            f"<p class='skipped'>skipped: {escape(report.skip_reason)}</p>"
        )
    dir_note = ("+1 (long-high: higher factor predicts higher forward "
                "returns)" if report.direction >= 0 else
                "-1 (long-low: LOWER factor predicts higher forward "
                "returns; tables below use raw factor values, signs NOT "
                "flipped)")
    return f"""
<h2>{escape(title)}</h2>
<p class="meta">n_obs = {report.n_obs} | quantiles = {report.quantiles} |
direction = {escape(dir_note)}</p>
<h3>Quantile Portfolio Returns</h3>
{_df_html(report.quantile_returns)}
<h3>IC by Period</h3>
{_df_html(report.ic_by_period)}
<h3>Quantile Turnover</h3>
{_df_html(report.quantile_turnover)}
<h3>Factor Rank Autocorrelation</h3>
{_df_html(report.rank_autocorrelation)}
"""


def generate_library_report(
    data: pd.DataFrame,
    out_path: str | None = None,
    quantiles: int = 5,
    periods: Sequence[int] = DEFAULT_PERIODS,
    rank_lags: Sequence[int] = DEFAULT_RANK_LAGS,
    min_obs: int = 50,
) -> dict[str, FactorReport]:
    """Run the report over every factor in the library on ``data``.

    Factors whose required columns are missing, whose computation fails,
    or which yield fewer than ``min_obs`` usable bars are skipped with a
    recorded reason (no exception propagates).  Emits one combined HTML
    document to ``out_path`` when given.

    Returns
    -------
    dict[str, FactorReport]
        name -> report (skipped factors included with ``skipped=True``).
    """
    reports: dict[str, FactorReport] = {}
    sections = []
    for name, spec in list_factors().items():
        reason = None
        if "close" not in data.columns:
            reason = "data has no 'close' column (needed for forward returns)"
        else:
            missing = [c for c in spec.required_columns if c not in data.columns]
            if missing:
                reason = f"missing required columns {missing}"
        if reason is None:
            try:
                factor = compute_factor(name, data)
                report = compute_factor_report(
                    factor, data["close"], name=name, direction=spec.direction,
                    quantiles=quantiles, periods=periods, rank_lags=rank_lags)
                if report.n_obs < min_obs:
                    report = FactorReport(
                        name=name, direction=spec.direction, n_obs=report.n_obs,
                        quantiles=quantiles, quantile_returns=pd.DataFrame(),
                        ic_by_period=pd.DataFrame(),
                        quantile_turnover=pd.DataFrame(),
                        rank_autocorrelation=pd.DataFrame(),
                        skipped=True,
                        skip_reason=f"only {report.n_obs} usable bars (< {min_obs})")
            except Exception as exc:  # noqa: BLE001 — degrade gracefully
                report = FactorReport(
                    name=name, direction=spec.direction, n_obs=0,
                    quantiles=quantiles, quantile_returns=pd.DataFrame(),
                    ic_by_period=pd.DataFrame(), quantile_turnover=pd.DataFrame(),
                    rank_autocorrelation=pd.DataFrame(),
                    skipped=True, skip_reason=f"{type(exc).__name__}: {exc}")
        else:
            report = FactorReport(
                name=name, direction=spec.direction, n_obs=0,
                quantiles=quantiles, quantile_returns=pd.DataFrame(),
                ic_by_period=pd.DataFrame(), quantile_turnover=pd.DataFrame(),
                rank_autocorrelation=pd.DataFrame(),
                skipped=True, skip_reason=reason)
        reports[name] = report
        sections.append(report_to_html(report, title=f"{name} — {spec.description}"))

    n_ok = sum(1 for r in reports.values() if not r.skipped)
    html = (
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        f"<title>Factor Library Report</title>{_CSS}</head><body>"
        f"<h1>Factor Library Report</h1>"
        f"<p class='meta'>{n_ok}/{len(reports)} factors reported; "
        f"quantiles={quantiles}; periods={tuple(periods)}. "
        "Forward return at bar t over horizon h = close[t+h]/close[t] − 1 "
        "(future prices used only as the target).</p>"
        + "\n".join(sections)
        + "</body></html>"
    )
    if out_path:
        with open(out_path, "w") as fh:
            fh.write(html)
    return reports
