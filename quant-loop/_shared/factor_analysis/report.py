"""General-purpose factor analysis report (A5).

Two complementary APIs in one module:

**Part 1 — Cross-sectional panel API**
    Accepts arbitrary ``factor`` and ``forward_returns`` Series with a
    ``(date, asset)`` MultiIndex.  Computes IC, quantile returns, turnover,
    and assembles a :class:`FactorAnalysisReport`.  Not coupled to the
    built-in factor library.

**Part 2 — Enhanced time-series API**
    Wraps :mod:`_shared.strategy_kit.factor_report` with cumulative quantile
    returns, rolling IC, a summary acceptance table, and HTML rendering.
    Works on single-asset time series with ``close`` prices.

References
----------
- Grinold & Kahn (2000), *Active Portfolio Management* — IC / IR framework.
- alphalens (Quantopian) — Tearsheet methodology.
- López de Prado (2018), *Advances in Financial Machine Learning*, Ch. 7–8.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from html import escape
from typing import Sequence

import numpy as np
import pandas as pd
from scipy.stats import spearmanr, t as student_t

from _shared.strategy_kit.factor_library import compute_factor, list_factors
from _shared.strategy_kit.factor_report import (
    DEFAULT_PERIODS,
    DEFAULT_RANK_LAGS,
    FactorReport,
    _assign_quantiles,
    _forward_returns,
    _ic_decay_table,
    _quantile_return_table,
    _quantile_turnover,
    _rank_autocorrelation,
)

@dataclass(frozen=True)
class ICStats:
    """Aggregated Information Coefficient statistics."""

    ic_mean: float = 0.0
    ic_std: float = 0.0
    icir: float = 0.0
    t_stat: float = 0.0
    hit_rate: float = 0.0
    n_periods: int = 0

__all__ = [
    # Part 1 — cross-sectional
    "FactorAnalysisReport",
    "ICStats",
    "compute_ic",
    "quantile_returns",
    "compute_turnover",
    "generate_report",
    # Part 2 — enhanced time-series
    "EnhancedFactorReport",
    "cumulative_quantile_returns",
    "rolling_ic",
    "summary_table",
    "compute_enhanced_report",
    "enhanced_report_to_html",
    "generate_enhanced_report",
]


# ===================================================================
# Part 1 — Cross-sectional panel API
# ===================================================================

def _is_cross_sectional(idx: pd.Index) -> bool:
    """True if *idx* is a MultiIndex suitable for per-date grouping."""
    return isinstance(idx, pd.MultiIndex) and len(idx.names) >= 2


def _hit_rate(factor: pd.Series, returns: pd.Series) -> float:
    """Fraction of observations where ``sign(factor) == sign(return)``."""
    mask = (factor != 0) & (returns != 0)
    if not mask.any():
        return 0.0
    return float((np.sign(factor[mask]) == np.sign(returns[mask])).mean())


def compute_ic(factor: pd.Series, forward_returns: pd.Series) -> dict:
    """Information Coefficient and related statistics.

    For a cross-sectional panel ``(date, asset)`` the IC is the per-date
    Spearman rank correlation, aggregated across dates.  For a flat series a
    single IC is computed.

    Returns dict with keys: ``ic_mean``, ``ic_std``, ``icir``, ``t_stat``,
    ``hit_rate``, ``n_periods``.
    """
    df = pd.concat(
        [factor.rename("f"), forward_returns.rename("r")], axis=1,
    ).dropna()

    if len(df) < 3:
        return {"ic_mean": 0.0, "ic_std": 0.0, "icir": 0.0,
                "t_stat": 0.0, "hit_rate": 0.0, "n_periods": 0}

    hit = _hit_rate(df["f"], df["r"])

    if _is_cross_sectional(df.index):
        ic_values: list[float] = []
        for _, group in df.groupby(level=0):
            if len(group) < 3:
                continue
            if group["f"].nunique() < 2 or group["r"].nunique() < 2:
                continue
            rho = spearmanr(group["f"], group["r"]).statistic
            if np.isfinite(rho):
                ic_values.append(float(rho))

        if not ic_values:
            return {"ic_mean": 0.0, "ic_std": 0.0, "icir": 0.0,
                    "t_stat": 0.0, "hit_rate": hit, "n_periods": 0}

        ic_arr = np.array(ic_values)
        n = len(ic_arr)
        ic_mean = float(np.mean(ic_arr))
        ic_std = float(np.std(ic_arr, ddof=1)) if n > 1 else 0.0
        icir = ic_mean / ic_std if ic_std > 0 else 0.0
        t_stat = ic_mean / (ic_std / np.sqrt(n)) if ic_std > 0 and n > 0 else 0.0
        return {"ic_mean": ic_mean, "ic_std": ic_std, "icir": icir,
                "t_stat": t_stat, "hit_rate": hit, "n_periods": n}

    # Flat index — single IC.
    if df["f"].nunique() < 2 or df["r"].nunique() < 2:
        return {"ic_mean": 0.0, "ic_std": 0.0, "icir": 0.0,
                "t_stat": 0.0, "hit_rate": hit, "n_periods": 0}
    rho = float(spearmanr(df["f"], df["r"]).statistic)
    return {"ic_mean": rho, "ic_std": 0.0, "icir": 0.0,
            "t_stat": 0.0, "hit_rate": hit, "n_periods": 1}


def quantile_returns(
    factor: pd.Series,
    forward_returns: pd.Series,
    n_quantiles: int = 5,
) -> pd.DataFrame:
    """Mean forward return per factor quantile + ``spread`` row.

    Index: ``Q1 … Qn, spread``; column: ``mean_return``.
    """
    df = pd.concat(
        [factor.rename("f"), forward_returns.rename("r")], axis=1,
    ).dropna()

    if len(df) < n_quantiles:
        return pd.DataFrame(columns=["mean_return"])

    if _is_cross_sectional(df.index):
        df["q"] = df.groupby(level=0)["f"].transform(
            lambda x: pd.qcut(x, n_quantiles, labels=False, duplicates="drop"),
        )
    else:
        df["q"] = pd.qcut(df["f"], n_quantiles, labels=False, duplicates="drop")

    df = df.dropna(subset=["q"])
    if len(df) == 0:
        return pd.DataFrame(columns=["mean_return"])

    means = df.groupby("q")["r"].mean()
    labels = [f"Q{int(q) + 1}" for q in means.index]
    result = pd.DataFrame({"mean_return": means.values}, index=labels)

    spread = float(means.iloc[-1] - means.iloc[0])
    result.loc["spread", "mean_return"] = spread
    return result


def compute_turnover(
    factor: pd.Series,
    n_quantiles: int = 5,
) -> pd.DataFrame:
    """Average quantile membership turnover between consecutive dates.

    For quantile *q*, turnover = fraction of assets in *q* at time *t* that
    were in a *different* quantile at *t − 1*, averaged over transitions.

    Returns an **empty** DataFrame when the index is not a cross-sectional
    MultiIndex (turnover is undefined for a single time series).

    Column: ``mean_turnover``; index: ``Q1 … Qn``.
    """
    f = factor.astype(float).replace([np.inf, -np.inf], np.nan).dropna()
    if len(f) == 0 or not _is_cross_sectional(f.index):
        return pd.DataFrame(columns=["mean_turnover"])

    q = f.groupby(level=0).transform(
        lambda x: pd.qcut(x, n_quantiles, labels=False, duplicates="drop"),
    ).astype(float)

    dates = q.index.get_level_values(0).unique()
    if len(dates) < 2:
        unique_qs = sorted(q.dropna().unique())
        return pd.DataFrame({
            "mean_turnover": {f"Q{int(qi) + 1}": 0.0 for qi in unique_qs},
        })

    # Unstack to (date × asset) for vectorised diff.
    q_wide = q.unstack(level=1)
    prev_wide = q_wide.shift(1)
    valid = prev_wide.notna() & q_wide.notna()
    changed = (q_wide != prev_wide) & valid

    turnover: dict[str, float] = {}
    for qi in range(n_quantiles):
        in_q = (q_wide == qi) & valid
        n_in_q = int(in_q.to_numpy().sum())
        n_changed = int(changed.to_numpy()[in_q.to_numpy()].sum())
        turnover[f"Q{qi + 1}"] = (
            float(n_changed / n_in_q) if n_in_q > 0 else 0.0
        )

    return pd.DataFrame({"mean_turnover": turnover})


@dataclass(frozen=True)
class ICStats:
    """Information Coefficient summary statistics.

    Wraps the IC/ICIR/t-stat/hit-rate metrics returned by
    :func:`compute_ic` into a typed, frozen result.
    """

    ic_mean: float
    ic_std: float
    icir: float
    t_stat: float
    hit_rate: float
    n_periods: int

    @classmethod
    def from_dict(cls, d: dict) -> ICStats:
        """Build from a :func:`compute_ic` output dict."""
        return cls(
            ic_mean=float(d.get("ic_mean", 0.0)),
            ic_std=float(d.get("ic_std", 0.0)),
            icir=float(d.get("icir", 0.0)),
            t_stat=float(d.get("t_stat", 0.0)),
            hit_rate=float(d.get("hit_rate", 0.0)),
            n_periods=int(d.get("n_periods", 0)),
        )


@dataclass(frozen=True)
class FactorAnalysisReport:
    """Full cross-sectional factor evaluation report."""

    name: str
    ic_stats: ICStats
    quantile_returns: pd.DataFrame
    turnover: pd.DataFrame
    spread: float


def generate_report(
    factor: pd.Series,
    forward_returns: pd.Series,
    name: str = "",
    n_quantiles: int = 5,
) -> FactorAnalysisReport:
    """Combine IC stats, quantile returns, and turnover into one report."""
    ic = compute_ic(factor, forward_returns)
    qr = quantile_returns(factor, forward_returns, n_quantiles=n_quantiles)
    to = compute_turnover(factor, n_quantiles=n_quantiles)

    spread = 0.0
    if "spread" in qr.index:
        spread = float(qr.loc["spread", "mean_return"])
    elif len(qr) >= 2:
        vals = qr["mean_return"]
        spread = float(vals.iloc[-1] - vals.iloc[0])

    return FactorAnalysisReport(
        name=name,
        ic_stats=ICStats(
            ic_mean=ic["ic_mean"],
            ic_std=ic["ic_std"],
            icir=ic["icir"],
            t_stat=ic["t_stat"],
            hit_rate=ic["hit_rate"],
            n_periods=ic["n_periods"],
        ),
        quantile_returns=qr,
        turnover=to,
        spread=spread,
    )


# ===================================================================
# Part 2 — Enhanced time-series API
# ===================================================================

def cumulative_quantile_returns(
    factor: pd.Series,
    close: pd.Series,
    quantiles: int = 5,
    horizon: int = 1,
) -> pd.DataFrame:
    """Equity-curve (cumulative return) of each quantile portfolio.

    Returns a DataFrame with columns ``Q1 … Qq`` indexed by bar timestamp.
    """
    f = factor.astype(float).replace([np.inf, -np.inf], np.nan)
    aligned = pd.concat([f.rename("f"), close.astype(float).rename("c")], axis=1)
    aligned = aligned.dropna()
    if len(aligned) < 20:
        raise ValueError(f"insufficient observations: {len(aligned)}")

    fwd = (aligned["c"].shift(-horizon) / aligned["c"] - 1.0).rename("fwd")
    q = _assign_quantiles(aligned["f"], quantiles)
    df = pd.DataFrame({"q": q, "fwd": fwd}).dropna()

    cum = {}
    for v in sorted(df["q"].unique()):
        mask = df["q"] == v
        r = df.loc[mask, "fwd"].copy()
        cum[f"Q{int(v) + 1}"] = (1.0 + r).cumprod() - 1.0
    return pd.DataFrame(cum, index=df.index)


def rolling_ic(
    factor: pd.Series,
    close: pd.Series,
    horizon: int = 1,
    window: int = 60,
) -> pd.Series:
    """Rolling Spearman rank IC between factor and forward returns.

    Uses a vectorised loop over numpy slices for speed and correctness
    (``DataFrame.rolling().apply`` with ``raw=False`` passes columns
    individually, not as a block, so it cannot compute a bivariate IC).
    """
    f = factor.astype(float).replace([np.inf, -np.inf], np.nan)
    fwd = close.astype(float).shift(-horizon) / close.astype(float) - 1.0
    df = pd.DataFrame({"f": f, "r": fwd}).dropna()

    n = len(df)
    result = pd.Series(np.nan, index=df.index, name="ic", dtype=float)
    f_vals = df["f"].to_numpy()
    r_vals = df["r"].to_numpy()

    for i in range(window - 1, n):
        bf = f_vals[i - window + 1 : i + 1]
        br = r_vals[i - window + 1 : i + 1]
        if len(bf) < 10:
            continue
        uf = np.unique(bf)
        if len(uf) < 2:
            continue
        ur = np.unique(br)
        if len(ur) < 2:
            continue
        rho = float(spearmanr(bf, br).statistic)
        if np.isfinite(rho):
            result.iloc[i] = rho

    return result


# -----------------------------------------------------------------------
# Summary metrics
# -----------------------------------------------------------------------

@dataclass(frozen=True)
class _SummaryMetrics:
    horizon: int
    n_obs: int
    ic_mean: float
    ic_std: float
    icir: float
    t_stat: float
    p_value: float
    hit_rate: float
    spread_annual: float


def summary_table(
    factor: pd.Series,
    close: pd.Series,
    periods: Sequence[int] = DEFAULT_PERIODS,
    quantiles: int = 5,
    periods_per_year: int = 365,
) -> pd.DataFrame:
    """Compact acceptance-checklist table — one row per horizon.

    Columns: ``n_obs, ic_mean, ic_std, icir, t_stat, p_value,
    hit_rate, spread_annual``.
    """
    f = factor.astype(float).replace([np.inf, -np.inf], np.nan)
    c = close.astype(float)
    aligned = pd.concat([f.rename("f"), c.rename("c")], axis=1).dropna()
    if len(aligned) < 20:
        raise ValueError(f"insufficient observations: {len(aligned)}")

    rows: list[_SummaryMetrics] = []
    fwd_all = _forward_returns(c, periods)
    for h in periods:
        r = fwd_all[h]
        df = pd.DataFrame({"f": f, "r": r}).dropna()
        n = len(df)
        if n < 10 or df["f"].nunique() < 2 or df["r"].nunique() < 2:
            rows.append(_SummaryMetrics(
                horizon=h, n_obs=n, ic_mean=np.nan, ic_std=np.nan,
                icir=np.nan, t_stat=np.nan, p_value=np.nan,
                hit_rate=np.nan, spread_annual=np.nan))
            continue

        ic_vals: list[float] = []
        chunks = np.array_split(np.arange(n), min(10, n))
        for idx in chunks:
            if len(idx) < 5:
                continue
            sub = df.iloc[idx]
            if sub["f"].nunique() < 2 or sub["r"].nunique() < 2:
                continue
            ic_vals.append(float(spearmanr(sub["f"], sub["r"]).statistic))
        ic_arr = np.array(ic_vals) if ic_vals else np.array([np.nan])
        ic_mean = float(np.mean(ic_arr))
        ic_std = float(np.std(ic_arr, ddof=1)) if len(ic_arr) > 1 else np.nan
        icir = ic_mean / ic_std if ic_std and ic_std > 0 else np.nan

        if len(ic_arr) > 1 and ic_std and ic_std > 0:
            t_stat = float(ic_mean / (ic_std / np.sqrt(len(ic_arr))))
            p_val = float(2 * student_t.sf(abs(t_stat), df=len(ic_arr) - 1))
        else:
            t_stat = np.nan
            p_val = np.nan

        hits = (np.sign(df["f"]) == np.sign(df["r"])).sum()
        zero_mask = (df["f"] != 0) & (df["r"] != 0)
        hit_rate = float(hits / zero_mask.sum()) if zero_mask.sum() > 0 else np.nan

        q = _assign_quantiles(df["f"], min(quantiles, df["f"].nunique()))
        qr = pd.DataFrame({"q": q, "r": df["r"]}).dropna()
        means = qr.groupby("q")["r"].mean()
        spread = float(means.iloc[-1] - means.iloc[0]) if len(means) >= 2 else np.nan
        spread_annual = spread * periods_per_year / h if np.isfinite(spread) else np.nan

        rows.append(_SummaryMetrics(
            horizon=h, n_obs=n, ic_mean=ic_mean, ic_std=ic_std,
            icir=icir, t_stat=t_stat, p_value=p_val,
            hit_rate=hit_rate, spread_annual=spread_annual))

    df_out = pd.DataFrame([{
        "horizon": r.horizon, "n_obs": r.n_obs, "ic_mean": r.ic_mean,
        "ic_std": r.ic_std, "icir": r.icir, "t_stat": r.t_stat,
        "p_value": r.p_value, "hit_rate": r.hit_rate,
        "spread_annual": r.spread_annual,
    } for r in rows])
    return df_out.set_index("horizon")


# -----------------------------------------------------------------------
# Enhanced report (wraps existing FactorReport + new components)
# -----------------------------------------------------------------------

@dataclass(frozen=True)
class EnhancedFactorReport:
    """Full alphalens-style report: original 4 components + 3 new ones."""

    base: FactorReport
    cumulative_returns: pd.DataFrame
    ic_series: pd.Series
    summary: pd.DataFrame

    @property
    def n_obs(self) -> int:
        """Delegate to base FactorReport for convenience."""
        return self.base.n_obs


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
    """Thin re-export of the base compute_factor_report."""
    from _shared.strategy_kit.factor_report import (
        compute_factor_report as _base,
    )
    return _base(factor, close, name=name, direction=direction,
                 quantiles=quantiles, periods=periods,
                 rank_lags=rank_lags, ic_chunks=ic_chunks)


def compute_enhanced_report(
    factor: pd.Series,
    close: pd.Series,
    name: str | None = None,
    direction: int = 1,
    quantiles: int = 5,
    periods: Sequence[int] = DEFAULT_PERIODS,
    rank_lags: Sequence[int] = DEFAULT_RANK_LAGS,
    ic_chunks: int = 10,
    rolling_ic_window: int = 60,
    periods_per_year: int = 365,
) -> EnhancedFactorReport:
    """Full factor validation report (7 components)."""
    base = compute_factor_report(
        factor, close, name=name, direction=direction,
        quantiles=quantiles, periods=periods, rank_lags=rank_lags,
        ic_chunks=ic_chunks)
    cum = cumulative_quantile_returns(factor, close, quantiles=quantiles,
                                      horizon=periods[0])
    ic_ts = rolling_ic(factor, close, horizon=periods[0],
                      window=rolling_ic_window)
    summ = summary_table(factor, close, periods=periods,
                         quantiles=quantiles,
                         periods_per_year=periods_per_year)
    return EnhancedFactorReport(
        base=base, cumulative_returns=cum, ic_series=ic_ts, summary=summ)


# -----------------------------------------------------------------------
# HTML rendering
# -----------------------------------------------------------------------

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
.pass { color: #060; font-weight: bold; }
.fail { color: #a00; font-weight: bold; }
</style>
"""


def _df_html(df: pd.DataFrame, float_fmt: str = "{:.6f}") -> str:
    return df.to_html(float_format=lambda v: float_fmt.format(v), border=0)


def enhanced_report_to_html(
    report: EnhancedFactorReport,
    title: str | None = None,
) -> str:
    """Render the full 7-component report as self-contained HTML."""
    title = title or f"Enhanced Factor Report: {report.base.name}"
    if report.base.skipped:
        return (
            f"<h2>{escape(title)}</h2>"
            f"<p class='skipped'>skipped: {escape(report.base.skip_reason)}</p>"
        )

    def _badge(t_stat: float, p_val: float) -> str:
        if np.isnan(t_stat):
            return "<span class='meta'>n/a</span>"
        if p_val < 0.05:
            return "<span class='pass'>PASS</span>"
        return "<span class='fail'>FAIL</span>"

    summary_rows = []
    for h, row in report.summary.iterrows():
        summary_rows.append(
            f"<tr><td>{h}</td><td>{int(row['n_obs'])}</td>"
            f"<td>{row['ic_mean']:.6f}</td><td>{row['icir']:.6f}</td>"
            f"<td>{row['t_stat']:.4f}</td><td>{row['p_value']:.4f}</td>"
            f"<td>{row['hit_rate']:.4f}</td>"
            f"<td>{row['spread_annual']:.6f}</td>"
            f"<td>{_badge(row['t_stat'], row['p_value'])}</td></tr>"
        )

    summary_html = (
        "<table><thead><tr>"
        "<th>horizon</th><th>n_obs</th><th>ic_mean</th><th>icir</th>"
        "<th>t_stat</th><th>p_value</th><th>hit_rate</th>"
        "<th>spread_annual</th><th>verdict</th>"
        "</tr></thead><tbody>"
        + "\n".join(summary_rows)
        + "</tbody></table>"
    )

    ic_clean = report.ic_series.dropna()
    if len(ic_clean) > 0:
        ic_head = ic_clean.head(5).to_frame("ic").to_html(
            float_format=lambda v: f"{v:.6f}", border=0)
        ic_tail = ic_clean.tail(5).to_frame("ic").to_html(
            float_format=lambda v: f"{v:.6f}", border=0)
        ic_section = (
            f"<h3>Rolling IC (head 5)</h3>{ic_head}"
            f"<h3>Rolling IC (tail 5)</h3>{ic_tail}"
        )
    else:
        ic_section = "<h3>Rolling IC</h3><p class='meta'>all NaN</p>"

    return f"""
<h2>{escape(title)}</h2>
<p class="meta">n_obs = {report.base.n_obs} | quantiles = {report.base.quantiles} |
direction = {report.base.direction}</p>

<h3>Summary (Acceptance Checklist)</h3>
{summary_html}

<h3>Quantile Portfolio Returns</h3>
{_df_html(report.base.quantile_returns)}

<h3>IC by Period</h3>
{_df_html(report.base.ic_by_period)}

<h3>Quantile Turnover</h3>
{_df_html(report.base.quantile_turnover)}

<h3>Factor Rank Autocorrelation</h3>
{_df_html(report.base.rank_autocorrelation)}

<h3>Cumulative Quantile Returns (h={1})</h3>
{_df_html(report.cumulative_returns)}

{ic_section}
"""


def generate_enhanced_report(
    data: pd.DataFrame,
    out_path: str | None = None,
    quantiles: int = 5,
    periods: Sequence[int] = DEFAULT_PERIODS,
    rank_lags: Sequence[int] = DEFAULT_RANK_LAGS,
    min_obs: int = 50,
    rolling_ic_window: int = 60,
    periods_per_year: int = 365,
) -> dict[str, EnhancedFactorReport]:
    """Run the enhanced report over every factor in the library."""
    reports: dict[str, EnhancedFactorReport] = {}
    sections: list[str] = []

    for name, spec in list_factors().items():
        reason = None
        if "close" not in data.columns:
            reason = "data has no 'close' column"
        else:
            missing = [c for c in spec.required_columns if c not in data.columns]
            if missing:
                reason = f"missing required columns {missing}"
        if reason is None:
            try:
                factor = compute_factor(name, data)
                report = compute_enhanced_report(
                    factor, data["close"], name=name, direction=spec.direction,
                    quantiles=quantiles, periods=periods, rank_lags=rank_lags,
                    rolling_ic_window=rolling_ic_window,
                    periods_per_year=periods_per_year)
                if report.base.n_obs < min_obs:
                    base_skip = FactorReport(
                        name=name, direction=spec.direction,
                        n_obs=report.base.n_obs, quantiles=quantiles,
                        quantile_returns=pd.DataFrame(),
                        ic_by_period=pd.DataFrame(),
                        quantile_turnover=pd.DataFrame(),
                        rank_autocorrelation=pd.DataFrame(),
                        skipped=True,
                        skip_reason=f"only {report.base.n_obs} usable bars (< {min_obs})")
                    report = EnhancedFactorReport(
                        base=base_skip, cumulative_returns=pd.DataFrame(),
                        ic_series=pd.Series(dtype=float),
                        summary=pd.DataFrame())
            except Exception as exc:  # noqa: BLE001
                base_skip = FactorReport(
                    name=name, direction=spec.direction, n_obs=0,
                    quantiles=quantiles, quantile_returns=pd.DataFrame(),
                    ic_by_period=pd.DataFrame(),
                    quantile_turnover=pd.DataFrame(),
                    rank_autocorrelation=pd.DataFrame(),
                    skipped=True,
                    skip_reason=f"{type(exc).__name__}: {exc}")
                report = EnhancedFactorReport(
                    base=base_skip, cumulative_returns=pd.DataFrame(),
                    ic_series=pd.Series(dtype=float),
                    summary=pd.DataFrame())
        else:
            base_skip = FactorReport(
                name=name, direction=spec.direction, n_obs=0,
                quantiles=quantiles, quantile_returns=pd.DataFrame(),
                ic_by_period=pd.DataFrame(),
                quantile_turnover=pd.DataFrame(),
                rank_autocorrelation=pd.DataFrame(),
                skipped=True, skip_reason=reason)
            report = EnhancedFactorReport(
                base=base_skip, cumulative_returns=pd.DataFrame(),
                ic_series=pd.Series(dtype=float),
                summary=pd.DataFrame())

        reports[name] = report
        sections.append(
            enhanced_report_to_html(
                report, title=f"{name} — {spec.description}"))

    n_ok = sum(1 for r in reports.values() if not r.base.skipped)
    html = (
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        f"<title>Enhanced Factor Library Report</title>{_CSS}</head><body>"
        f"<h1>Enhanced Factor Library Report</h1>"
        f"<p class='meta'>{n_ok}/{len(reports)} factors reported; "
        f"quantiles={quantiles}; periods={tuple(periods)}.</p>"
        + "\n".join(sections)
        + "</body></html>"
    )
    if out_path:
        with open(out_path, "w") as fh:
            fh.write(html)
    return reports
