# Interface Contract — `slippage_sqrt_p7exec_027`

Stable public surface for downstream callers (live-trading harness,
paper-trading harness, pre-trade blocking, alerting, dashboards,
post-trade attribution review). Changes here require a version bump
on `KERNEL_VERSION` plus a one-line note in `SPEC.md` §1.

## 1. Public dataclasses

### `SlippageSqrtRequest`

```
fill_id                : str    required, non-empty, globally unique
strategy_id            : str    required, non-empty
symbol                 : str    required, non-empty, e.g. "BTCUSDT"
venue                  : str    required, non-empty, e.g. "binance"
side                   : "buy" | "sell"
qty                    : float  > 0
mid_price              : float  >= 0  (default 0.0 — informational only)
daily_volume           : float  > 0
volatility_per_s       : float  >= 0 (default 0.0 is allowed and yields impact=0)
arrival_horizon_s      : float  > 0   (default 1.0)
seconds_per_day        : float  > 0   (default 86400.0)
k_factor               : float  > 0   (default 1.0)
fee_bps                : float  >= 0  (default 0.0)
```

`__post_init__` validates fill_id, strategy_id, symbol, venue are
non-empty strings; side is buy/sell; qty > 0; mid_price >= 0;
daily_volume > 0; volatility_per_s >= 0; arrival_horizon_s > 0;
seconds_per_day > 0; k_factor > 0; fee_bps >= 0. Raises
`InvalidRequestError` (a ValueError) on violation.

### `SlippageSqrtEstimate`

```
fill_id                : str    mirrored
strategy_id            : str    mirrored
symbol                 : str    mirrored
venue                  : str    mirrored
side                   : str    mirrored
qty                    : float  mirrored
mid_price              : float  mirrored
daily_volume           : float  mirrored
arrival_horizon_s      : float  mirrored
seconds_per_day        : float  mirrored
k_factor               : float  mirrored
volatility_per_s       : float  mirrored
v_per_s                : float  daily_volume / seconds_per_day
participation          : float  qty / (v_per_s * arrival_horizon_s)
temporary_impact_bps   : float  k * sigma * sqrt(participation) * 10000
fee_bps                : float  mirrored
total_slippage_bps     : float  temporary_impact_bps + fee_bps
verdict                : str    one of VERDICT_* constants
decided_at_ms          : int    epoch ms when computed
kernel_version         : str    semver, e.g. "0.1.0"
```

`temporary_impact_bps` is non-negative (`>= 0`). `total_slippage_bps`
is `temporary_impact_bps + fee_bps`; with `fee_bps = 0` the two are
equal. `verdict` is keyed off `temporary_impact_bps` only — a fee is
a known cost, not an impact surprise.

## 2. Public classes

### `SlippageSqrtCalculator(journal_dir, *, checkpoint_every=100, kernel_version=KERNEL_VERSION)`

Constructs and opens the WAL. Rehydrates from the journal if a
checkpoint exists; raises `SlippageSqrtJournalReplayRequired` if the
journal exists without a checkpoint (use `rebuild_checkpoint.py` to
materialise one).

| Method | Returns | Thread-safety |
|--------|---------|---------------|
| `estimate(req: SlippageSqrtRequest) -> SlippageSqrtEstimate` | estimate for this request | Single-writer; uses internal RLock |
| `stats() -> Mapping[str, object]` | aggregate counters (global + per-symbol + per-verdict) | Multi-reader |
| `stats_for(symbol: str) -> Mapping[str, object]` | per-symbol counters | Multi-reader |
| `cumulative_impact_bps_for(symbol: str) -> float` | cumulative `temporary_impact_bps` over all journaled fills on `symbol` | Multi-reader |
| `known_symbols() -> list[str]` | sorted list of symbols seen | Multi-reader |
| `close()` | None | Single-writer |
| Context manager (`__enter__`/`__exit__`) | self | — |

### `ExecutionRunner(journal_dir, **kwargs)`

Thin wrapper that owns one `SlippageSqrtCalculator` and verdict
tally counters.

| Method | Returns | Thread-safety |
|--------|---------|---------------|
| `estimate(req) -> SlippageSqrtEstimate` | estimate for this request | Single-writer |
| `stats()` | snapshot of counters | Multi-reader |
| `stats_for(symbol)` | per-symbol counters | Multi-reader |
| `cumulative_impact_bps_for(symbol)` | cumulative impact bps | Multi-reader |
| `known_symbols()` | sorted symbols | Multi-reader |
| `calculator() -> SlippageSqrtCalculator` | underlying calculator | Multi-reader |
| `shutdown()` | None | Single-writer |

### `LiveExecutionRunner(journal_dir)` (in `execution/runner.py`)

Top-level orchestrator. Wires `ExecutionRunner` (the slippage-sqrt
calculator) into the live-trading runner. The wire-in is exposed
only via a dedicated `ingest_slippage_sqrt_request(req)` method
because the calculator requires `daily_volume` and
`volatility_per_s`, which neither `OrderEvent` nor `FillRecord`
provides.

| Method | Returns | Thread-safety |
|--------|---------|---------------|
| `ingest_slippage_sqrt_request(req) -> SlippageSqrtEstimate` | estimate for this request | Single-writer |
| `slippage_sqrt_stats() -> Mapping[str, Mapping[str, object]]` | per-component counters | Multi-reader |
| `shutdown()` | None | Single-writer |

Existing `ingest(event)` / `ingest_fill(fill)` / `stats()` /
`shutdown()` methods are preserved unchanged. There is **no**
`ingest_fill` → `slippage_sqrt` bridge; the calculator would need
to invent daily-volume and volatility numbers from `FillRecord` and
that would silently corrupt the impact estimate. Callers that have
the full context should call `ingest_slippage_sqrt_request(req)`
once per fill.

## 3. Public enums / constants

### `Side`

`"buy"`, `"sell"`. Plain string literals; no separate enum class.

### Verdict vocabulary

- `VERDICT_MINIMAL = "minimal"` — `< 5.0` bps temporary impact
- `VERDICT_LOW = "low"` — `[5.0, 15.0)` bps
- `VERDICT_MODERATE = "moderate"` — `[15.0, 50.0)` bps
- `VERDICT_HIGH = "high"` — `[50.0, 200.0)` bps
- `VERDICT_EXTREME = "extreme"` — `>= 200.0` bps

Plain `str` constants.

## 4. Public exceptions

| Exception | When |
|-----------|------|
| `SlippageSqrtError` | base class for every calculator-raised error |
| `InvalidRequestError` | malformed `SlippageSqrtRequest` (also a ValueError) |
| `SlippageSqrtJournalWriteError` | journal append failed (disk full, fd closed, ...) |
| `SlippageSqrtJournalReplayRequired` | construction with journal present but no checkpoint |
| `SlippageSqrtHalt` | corrupted journal line during replay |

All five inherit from `SlippageSqrtError` so callers can catch the
base class for "any calculator problem".

## 5. Public constants

| Name | Value |
|------|-------|
| `KERNEL_VERSION` | `"0.1.0"` |
| `DEFAULT_CHECKPOINT_EVERY` | `100` |
| `DEFAULT_SECONDS_PER_DAY` | `86400.0` |
| `DEFAULT_ARRIVAL_HORIZON_S` | `1.0` |
| `DEFAULT_K_FACTOR` | `1.0` |
| `VERDICT_THRESHOLD_MINIMAL` | `5.0` bps |
| `VERDICT_THRESHOLD_LOW` | `15.0` bps |
| `VERDICT_THRESHOLD_MODERATE` | `50.0` bps |
| `VERDICT_THRESHOLD_HIGH` | `200.0` bps |
| `JOURNAL_FILENAME` | `"journal.jsonl"` |
| `CHECKPOINT_FILENAME` | `"state.json"` |

## 6. Stability guarantees

- Verdict vocabulary is **additive** within a major version. New
  verdicts may be added in minor releases; existing verdicts will
  never be renamed or removed.
- `SlippageSqrtRequest`, `SlippageSqrtEstimate` field names are
  **frozen**. New fields may be added (with defaults) in minor
  releases.
- Almgren factorisation (`temporary_impact_bps = k * σ * sqrt(Q /
  (V_per_s * T)) * 10000`) is **frozen** for the lifetime of the
  major version. Any change requires a new folder suffix.
- `KERNEL_VERSION` follows semver. Breaking changes bump the major
  version and are recorded in `SPEC.md` §1.

## 7. Error types

- `SlippageSqrtJournalWriteError` — raised when WAL append fails.
  The runner halts. We never silently swallow this.
- `SlippageSqrtHalt` — raised on corrupted journal during replay.
  The runner halts.
- `SlippageSqrtJournalReplayRequired` — raised on construction with
  journal present but no checkpoint. Recovery: run
  `rebuild_checkpoint.py`.
- `InvalidRequestError` (a ValueError) — bad input to
  `SlippageSqrtRequest`. The runner treats these as caller bugs;
  they are NOT caught and converted to estimates.
