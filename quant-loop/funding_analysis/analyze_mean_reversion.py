"""Mean reversion analysis for extreme funding rate events on BTC/ETH/SOL.

Definitions
-----------
* Extreme event     : |fundingRate| >= 0.001  (= 0.10% / 10 bp per 8h funding print)
* Reversion (4h)    : sign flips OR |rate| drops below 0.0003 (0.03% / 3 bp)
                      at the next funding print that lies within +4h of the event
* Next-print proxy  : the very next funding print, regardless of cadence gap
                      (used because Binance funding is 8h-cadence — strictly
                      "within 4h" is unobservable without sub-8h prints)

Two views
---------
1. Per-event  : every row with |rate|>=0.1% is its own event. Sub-8h prints
                (e.g. FTX-collapse emergency funding on SOL 2022-11) mean
                the "next tick" can be minutes later, giving a very strict
                immediate-reversion test.
2. Per-episode: coalesce contiguous extremes (any 24h cluster of ≥1 extreme
                rows is treated as one episode, anchored at the first
                extreme). Avoids double-counting sub-8h bursts.

Output: prints a tabular summary to stdout and writes JSON for the report.
"""

import json
from pathlib import Path

import numpy as np
import pandas as pd

DATA_DIR = Path("/home/smark/multica/quant-loop/data/funding")
OUT_DIR = Path("/home/smark/multica/quant-loop/funding_analysis")
OUT_DIR.mkdir(parents=True, exist_ok=True)

EXTREME_THRESH = 0.001      # 0.10%  (10 bp)
REVERT_THRESH  = 0.0003     # 0.03%  (3 bp)
LOOKAHEAD_HOURS = 4         # request says "within 4h"
NEXT_PRINT_LIMIT_HOURS = 24 # cap "next tick" window to 24h to avoid stale leaks


def load_symbol(sym: str) -> pd.DataFrame:
    df = pd.read_parquet(DATA_DIR / f"{sym}.parquet")
    df = df.sort_values("ts").reset_index(drop=True)
    df["dt_h"] = df["ts"].diff().dt.total_seconds() / 3600.0
    return df


def next_within_4h(row: pd.Series, df: pd.DataFrame, lookahead_h: float) -> pd.Series | None:
    """Return the next funding print whose timestamp is < lookahead_h after row.ts.
    The next print must come strictly after row.ts. Returns None if there is no
    such print within +24h (defensive cap so a missing print doesn't get treated
    as 'reverted' or 'not' based on data gap)."""
    row_t = row["ts"]
    later = df[df["ts"] > row_t]
    if later.empty:
        return None
    # gap from event to next print
    gap = (later["ts"].iloc[0] - row_t).total_seconds() / 3600.0
    if gap > NEXT_PRINT_LIMIT_HOURS:
        return None
    in_window = later[later["ts"] <= row_t + pd.Timedelta(hours=lookahead_h)]
    if not in_window.empty:
        return in_window.iloc[0]
    return None  # next print exists but is beyond 4h window; treat as not-yet-observed


def next_print(row: pd.Series, df: pd.DataFrame) -> pd.Series | None:
    """Return the very next funding print, capped at +24h for safety."""
    row_t = row["ts"]
    later = df[df["ts"] > row_t]
    if later.empty:
        return None
    gap = (later["ts"].iloc[0] - row_t).total_seconds() / 3600.0
    if gap > NEXT_PRINT_LIMIT_HOURS:
        return None
    return later.iloc[0]


def reverts(prev_rate: float, next_rate: float, revert_thresh: float) -> bool:
    """Reversion = sign flipped OR |next| < revert_thresh."""
    if pd.isna(prev_rate) or pd.isna(next_rate):
        return False
    sign_flip = (prev_rate > 0 and next_rate < 0) or (prev_rate < 0 and next_rate > 0)
    magnitude_dropped = abs(next_rate) < revert_thresh
    return sign_flip or magnitude_dropped


def classify_event(rate: float) -> str:
    if pd.isna(rate):
        return "non-extreme"
    if rate > EXTREME_THRESH:
        return "pos"
    if rate < -EXTREME_THRESH:
        return "neg"
    return "non-extreme"


def analyze_symbol(df: pd.DataFrame, sym: str) -> dict:
    df["side"] = df["fundingRate"].apply(classify_event)
    extreme = df[df["side"] != "non-extreme"].copy()

    # Per-event reversion (next print, all ticks)
    per_event_records = []
    for idx, row in extreme.iterrows():
        nxt = next_print(row, df)
        rev = reverts(row["fundingRate"], nxt["fundingRate"], REVERT_THRESH) if nxt is not None else None
        gap_h = None if nxt is None else (nxt["ts"] - row["ts"]).total_seconds() / 3600.0
        per_event_records.append({
            "ts": row["ts"].isoformat(),
            "rate": row["fundingRate"],
            "side": row["side"],
            "next_ts": nxt["ts"].isoformat() if nxt is not None else None,
            "next_rate": nxt["fundingRate"] if nxt is not None else None,
            "next_gap_h": gap_h,
            "rev_within_next": rev,
            "rev_within_4h": None,  # populated below when applicable
        })
        # also test strict 4h window
        nxt4 = next_within_4h(row, df, LOOKAHEAD_HOURS)
        per_event_records[-1]["rev_within_4h"] = (
            reverts(row["fundingRate"], nxt4["fundingRate"], REVERT_THRESH)
            if nxt4 is not None else None
        )

    # Per-episode reversion: coalesce contiguous extremes within 24h
    extreme_sorted = extreme.sort_values("ts").reset_index()
    episodes = []
    cur = None
    for _, row in extreme_sorted.iterrows():
        if cur is None:
            cur = {"start_idx": row["index"], "first_ts": row["ts"], "last_ts": row["ts"],
                   "first_rate": row["fundingRate"], "side": row["side"]}
        else:
            gap_h = (row["ts"] - cur["last_ts"]).total_seconds() / 3600.0
            if gap_h <= 24:
                cur["last_ts"] = row["ts"]
            else:
                episodes.append(cur)
                cur = {"start_idx": row["index"], "first_ts": row["ts"], "last_ts": row["ts"],
                       "first_rate": row["fundingRate"], "side": row["side"]}
    if cur is not None:
        episodes.append(cur)

    # For each episode, find FIRST print after episode start where rate reverts
    ep_records = []
    for ep in episodes:
        # Use the original df to find events after episode end
        later = df[df["ts"] >= ep["last_ts"]]
        rev_print = None
        for _, crow in later.iterrows():
            if reverts(ep["first_rate"], crow["fundingRate"], REVERT_THRESH):
                rev_print = crow
                break
        ep_first_revert_ts = rev_print["ts"] if rev_print is not None else None
        ep_first_revert_gap_h = (
            (ep_first_revert_ts - ep["first_ts"]).total_seconds() / 3600.0
            if ep_first_revert_ts is not None else None
        )
        ep_records.append({
            "start_ts": ep["first_ts"].isoformat(),
            "first_rate": ep["first_rate"],
            "side": ep["side"],
            "first_revert_ts": ep_first_revert_ts.isoformat() if ep_first_revert_ts is not None else None,
            "first_revert_gap_h": ep_first_revert_gap_h,
            "reverted_within_4h": (
                ep_first_revert_gap_h is not None and ep_first_revert_gap_h <= 4
            ),
            "reverted_within_24h": (
                ep_first_revert_gap_h is not None and ep_first_revert_gap_h <= 24
            ),
        })

    return {
        "symbol": sym,
        "n_rows": int(len(df)),
        "n_extreme_events": int(len(per_event_records)),
        "n_extreme_episodes": int(len(ep_records)),
        "events": per_event_records,
        "episodes": ep_records,
    }


def summarize(results: dict) -> pd.DataFrame:
    rows = []
    for sym in ("BTCUSDT", "ETHUSDT", "SOLUSDT"):
        r = results[sym]
        ev = pd.DataFrame(r["events"])
        ep = pd.DataFrame(r["episodes"])

        def bucket(df, side, col):
            sub = df[df["side"] == side]
            if sub.empty:
                return dict(n=0)
            res = dict(n=len(sub))
            if col in sub:
                obs = sub[col].dropna()
                if not obs.empty:
                    res.update(
                        pct_within_4h=round(obs.mean() * 100, 1),
                        n_observed=int(len(obs)),
                    )
            return res

        # episode reversion (within 4h, within 24h)
        def ep_bucket(df, side, col):
            sub = df[df["side"] == side]
            if sub.empty:
                return dict(n=0)
            obs = sub[col].dropna() if col in sub else pd.Series([], dtype=bool)
            # boolean conversion: True counts as revert
            truthy = obs.astype(bool) if not obs.empty else pd.Series([], dtype=bool)
            return dict(
                n=len(sub),
                pct=round(truthy.mean() * 100, 1) if len(truthy) else None,
                n_with_revert=int(truthy.sum()) if len(truthy) else 0,
                median_gap_h=(
                    sub.loc[truthy.index, "first_revert_gap_h"].median()
                    if truthy.any() else None
                ),
            )

        rows.append({
            "symbol": sym,
            **bucket(ev, "pos", "rev_within_4h"),
            **{f"pos_ep_{k}": v for k, v in ep_bucket(ep, "pos", "reverted_within_4h").items()},
            **bucket(ev, "neg", "rev_within_4h"),
            **{f"neg_ep_{k}": v for k, v in ep_bucket(ep, "neg", "reverted_within_4h").items()},
            "total_events": r["n_extreme_events"],
            "total_episodes": r["n_extreme_episodes"],
        })
    return pd.DataFrame(rows)


def main():
    results = {}
    for sym in ("BTCUSDT", "ETHUSDT", "SOLUSDT"):
        df = load_symbol(sym)
        results[sym] = analyze_symbol(df, sym)

    summary = summarize(results)
    print("=== Per-symbol summary ===")
    print(summary.to_string(index=False))
    print()

    # Detailed per-episode listing
    for sym in ("BTCUSDT", "ETHUSDT", "SOLUSDT"):
        ep = pd.DataFrame(results[sym]["episodes"])
        if ep.empty:
            continue
        print(f"=== {sym} episodes ({len(ep)}) ===")
        for _, r in ep.iterrows():
            print(
                f"  start={r['start_ts']} first_rate={r['first_rate']:+.5f} "
                f"side={r['side']} -> first_revert_ts={r['first_revert_ts']} "
                f"gap_h={r['first_revert_gap_h']} "
                f"rev<4h={r['reverted_within_4h']} rev<24h={r['reverted_within_24h']}"
            )
        print()

    out_json = OUT_DIR / "mean_reversion_report.json"
    with open(out_json, "w") as f:
        json.dump(
            {
                "extreme_threshold": EXTREME_THRESH,
                "revert_threshold": REVERT_THRESH,
                "lookahead_hours": LOOKAHEAD_HOURS,
                "summary": summary.to_dict(orient="records"),
                "details": {
                    sym: {
                        "n_rows": r["n_rows"],
                        "n_extreme_events": r["n_extreme_events"],
                        "n_extreme_episodes": r["n_extreme_episodes"],
                        "episodes": r["episodes"],
                    }
                    for sym, r in results.items()
                },
            },
            f,
            indent=2,
            default=str,
        )
    print(f"Wrote {out_json}")


if __name__ == "__main__":
    main()
