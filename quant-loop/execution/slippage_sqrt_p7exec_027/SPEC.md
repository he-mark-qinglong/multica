# SPEC — `slippage_sqrt` (P7-EXEC-027)

Component for the [MAP-P7] Live Trading Infrastructure project
([SMA-35568](mention://issue/4de7fc07-765d-4908-bc5a-879d5ea7453d)),
sub-domain **B. Slippage Models**. Folder:
`~/multica/quant-loop/execution/slippage_sqrt_p7exec_027/`.

## 1. Purpose

Per-fill temporary market-impact estimator using the Almgren (2003)
square-root cost model. Each
`SlippageSqrtRequest` carries the fill context plus daily volume and
per-second volatility; the kernel returns a deterministic
`SlippageSqrtEstimate` (temporary impact in bps, total slippage in
bps with fee, magnitude verdict, kernel version, and bookkeeping
fields). Every estimate is journaled to the write-ahead log so the
cost-model history is durable across process restarts.

Estimator is purely observational — does not mutate order state,
send orders, throttle, re-route, contact the venue, or influence the
kill-switch. Downstream consumers (pre-trade blocking, alerting,
attribution review, dashboards) decide what to do with the estimate.

Hot-path-safe: p99 ≤ 250 µs Python overhead per `estimate()` call.
State persisted to write-ahead journal so the run history survives a
restart without losing entries. Aggregates (cumulative bps, per-verdict
tally, per-symbol total) are checkpointed so per-symbol dashboards
have a fast restart path.

## 2. Goals

1. Deterministic kernel — every estimate is a pure function of the
   request inputs; no I/O inside the math; no opaque randomness.
2. Almgren-form factorisation — temporary impact = `k * σ_per_s *
   sqrt(Q / (V_per_s * T))` expressed in bps. Conforms to the
   canonical Almgren square-root scaling; the empirical `k_factor`
   is exposed as a per-call override for calibration.
3. WAL-persistent — every estimate journaled BEFORE report returns;
   journal fsynced.
4. Hot-path under 250 µs Python overhead at p99 (single RLock, no
   I/O on the kernel path).
5. No silent drops — invalid inputs raise `InvalidRequestError`;
   journal-write failures raise `SlippageSqrtJournalWriteError`;
   replay-without-checkpoint raises
   `SlippageSqrtJournalReplayRequired`; corrupt journal raises
   `SlippageSqrtHalt`.
6. Restart-safe — close + re-construct rehydrates state from
   `state.json` if present, else from `journal.jsonl`. No double-count
   on fill_ids.

## 3. Non-Goals

- Not a permanent-impact / Almgren optimal-execution model. The
  square-root form covers only the temporary component;
  permanent-impact estimation is out of scope and tracked as a
  follow-up if added later.
- Not a cross-asset, lead-lag, or funding-impact model. Each
  `SlippageSqrtRequest` is processed in isolation with respect to
  other symbols.
- Does not send orders, cancel, re-route, or alter routing.
- Does not aggregate across venues; venue is preserved on the
  estimate as metadata for downstream consumers but is not used by
  the kernel itself.
- Does not perform its own vol estimation; the caller supplies
  `volatility_per_s` (e.g. from a realised-vol estimator).

## 4. Hot-path budget

| Metric                                  | Target                          |
|-----------------------------------------|---------------------------------|
| Kernel p99 overhead (compute + report)  | **≤ 250 µs** Python overhead    |
| Allocations per `estimate()` call       | ≤ 4 (one payload copy, one journal row, one verdict bump, one report) |
| Locking                                 | single RLock around the aggregate counters; no cross-symbol scans |
| Backpressure on journal failure         | raises `SlippageSqrtJournalWriteError` to caller — caller decides |
| Per-symbol memory                       | one aggregate row in the counters map; the kernel is otherwise stateless |

The 250 µs budget applies to the **kernel computation** of
`estimate(req)` (Almgren factorisation + verdict classification +
report build). The full `estimate()` call also performs a WAL write +
`os.fsync()` per request; that I/O cost is bounded by the storage
layer (typically 1 ms per `fsync` on rotating disks, microseconds on
tmpfs) and is not part of the kernel budget. The full-`estimate()`
wall-clock cost is observed and reported separately for transparency.

If the budget cannot be met in pure Python, the hot loop is eligible
to be re-implemented in Rust via PyO3 and exposed under the same
`SlippageSqrtCalculator.estimate` signature (out of scope; tracked as
a follow-up).

## 5. Inputs

### `SlippageSqrtRequest` (one request per fill)

```
fill_id                : str   globally unique (REQUIRED, non-empty)
strategy_id            : str   per-strategy aggregation key (REQUIRED, non-empty)
symbol                 : str   e.g. "BTCUSDT" (REQUIRED, non-empty)
venue                  : str   e.g. "binance" (REQUIRED, non-empty)
side                   : "buy" | "sell"  (REQUIRED)
qty                    : float > 0   (REQUIRED)
mid_price              : float > 0   (optional, default 0.0 — stored on output for traceability; not used in bps computation)
daily_volume           : float > 0   (REQUIRED)
volatility_per_s       : float >= 0  (REQUIRED; 0 is allowed and produces impact=0)
arrival_horizon_s      : float > 0   (optional, default 1.0)
seconds_per_day        : float > 0   (optional, default 86400.0)
k_factor               : float > 0   (optional, default 1.0)
fee_bps                : float >= 0  (optional, default 0.0)
```

`__post_init__` validates fill_id, strategy_id, symbol, venue are
non-empty strings; side is one of {"buy", "sell"}; qty > 0;
mid_price >= 0; daily_volume > 0; volatility_per_s >= 0;
arrival_horizon_s > 0; seconds_per_day > 0; k_factor > 0;
fee_bps >= 0. Violations raise `InvalidRequestError` (a ValueError).

`volatility_per_s` is allowed to be exactly 0.0 (returns 0 impact,
verdict = "minimal"); this is required for unit tests (the parent
issue explicitly lists "sigma=0" as a failure-mode probe) and is
also the only sensible answer for a perfectly still market.

### Derived (computed by the kernel)

```
V_per_s         = daily_volume / seconds_per_day
participation   = qty / (V_per_s * arrival_horizon_s)
                = (qty * seconds_per_day) / (daily_volume * arrival_horizon_s)
sqrt_part       = sqrt(max(participation, 0.0))
temp_impact_bps = k_factor * volatility_per_s * sqrt_part * 10000.0
total_bps       = temp_impact_bps + fee_bps
```

`participation` is non-negative by construction (all inputs are > 0
or >= 0). The `max(...)` is defensive against float ULP that could
push `participation` negative in a corner case; the kernel never
raises on this path.

### Notes on units

- `qty` and `daily_volume` are in the same units (e.g. BTC).
- `volatility_per_s` is the standard deviation of log returns per
  second, expressed as a fraction (not bps). Example: a market with
  50% annualised vol ≈ 0.0001 per-second std dev.
- `arrival_horizon_s` is the planned execution horizon in seconds.
- `k_factor` lets the caller calibrate against empirical data
  (Almgren's Y ≈ 0.314 for round-trip impact on liquid equities;
  crypto typically 0.5–2.0). Default 1.0 gives the canonical
  square-root prediction.
- `fee_bps` is the actual venue fee charged (fills that net
  rebates are negative; the kernel passes it through unchanged).

## 6. Outputs (`SlippageSqrtEstimate`)

```
fill_id                : str   mirrored
strategy_id            : str   mirrored
symbol                 : str   mirrored
venue                  : str   mirrored
side                   : str   mirrored
qty                    : float mirrored
mid_price              : float mirrored
daily_volume           : float mirrored
arrival_horizon_s      : float mirrored
seconds_per_day        : float mirrored
k_factor               : float mirrored
volatility_per_s       : float mirrored
v_per_s                : float   V_per_s = daily_volume / seconds_per_day
participation          : float   Q / (V_per_s * T)
temporary_impact_bps   : float   kernel output (adverse-positive bps)
fee_bps                : float   mirrored
total_slippage_bps     : float   temporary_impact_bps + fee_bps
verdict                : str     one of VERDICT_* constants
decided_at_ms          : int     epoch ms when computed
kernel_version         : str     semver, e.g. "0.1.0"
```

The estimate is signed adverse-positive (`temporary_impact_bps >= 0`).
A magnitude of zero is valid (the kernel hits zero naturally when
`participation = 0` or `volatility_per_s = 0`); it is not a
"missing" value. The verdict vocabulary is keyed off
`temporary_impact_bps` only (fee bps are reported but not used for
classification — a fee is a known cost, not an impact surprise).

### Verdict vocabulary

| Verdict     | temporary_impact_bps range | Meaning                                              |
|-------------|----------------------------|------------------------------------------------------|
| `minimal`   | `< 5.0`                    | Negligible impact; trade behaves like passive fill  |
| `low`       | `[5.0, 15.0)`              | Modest impact; typical small market orders          |
| `moderate`  | `[15.0, 50.0)`             | Significant impact; larger market orders            |
| `high`      | `[50.0, 200.0)`            | Large impact; trade exceeds light liquidity         |
| `extreme`   | `>= 200.0`                 | Very large impact; near the execution horizon limit |

The thresholds match what institutional execution desks use for
post-trade cost buckets. They are NOT trade-decision gates (the
component is observational).

## 7. Persistence contract (WAL)

Two artifacts under `journal_dir`:

- `journal.jsonl` — append-only estimate rows. Each line is JSON
  with `kind="estimate"` carrying a serialised `SlippageSqrtEstimate`
  AND the round-trip inputs (so post-trade reviewers can re-derive
  the bps number without ambiguity). Every `estimate()` call writes
  exactly one row, fsynced to disk.

- `state.json` — checkpointed view of the aggregate counters,
  written every N requests (default 100). On startup, calculator
  rehydrates by reading `state.json` if newer than `journal.jsonl`;
  otherwise it replays the journal from scratch — guaranteeing
  exactly-once counting of every request.

If `journal_dir` exists and `state.json` is missing or older than
`journal.jsonl`, the calculator raises
`SlippageSqrtJournalReplayRequired` rather than silently dropping
requests. Recovery: run the `rebuild_checkpoint` CLI helper.

## 8. Concurrency

- Single `threading.RLock` guards in-memory aggregate counters.
- Single-writer, multi-reader: only the thread that calls `estimate`
  should mutate counters. Multiple readers can call `stats_for` or
  `cumulative_bps_for(symbol)` concurrently.
- Journal file handle opened once at construction and held for the
  lifetime of the calculator. `fsync` after every write.
- `ExecutionRunner` wrapper adds its own `threading.Lock` to protect
  the verdict tally; does NOT touch calculator RLock.

## 9. Failure modes (covered by tests)

| Failure | Expected behaviour |
|---------|-------------------|
| Empty fill_id / strategy_id / symbol / venue | `InvalidRequestError` raised |
| `side` not in {buy, sell} | `InvalidRequestError` raised |
| `qty <= 0`, `daily_volume <= 0`, `arrival_horizon_s <= 0`, `seconds_per_day <= 0`, `k_factor <= 0` | `InvalidRequestError` raised |
| `volatility_per_s < 0`, `fee_bps < 0`, `mid_price < 0` | `InvalidRequestError` raised |
| `volatility_per_s == 0.0` | accepted; returns `temporary_impact_bps == 0.0`, verdict = "minimal" |
| `qty == 0` | rejected (InvalidRequestError); this is a different "0" from the sigma=0 case above |
| Disk full / journal write fails | `SlippageSqrtJournalWriteError` raised |
| Corrupted journal line on replay | `SlippageSqrtHalt` raised; do NOT silently skip |
| `state.json` missing but `journal.jsonl` present | `SlippageSqrtJournalReplayRequired` raised; recovery via `rebuild_checkpoint` |
| Duplicate fill_id re-applied on replay | skipped (idempotent on fill_id) |
| Estimate kernel math underflow / NaN / Inf | logged as `kernel_arithmetic_anomaly` in `stats()`; never silently skipped; the estimate still lands in the journal |

## 10. Rebuild helper (`rebuild_checkpoint.py`)

CLI tool to materialise a `state.json` from a `journal.jsonl`:

    python -m slippage_sqrt_p7exec_027.rebuild_checkpoint <journal_dir>

Used when the journal exists but the checkpoint has been lost (e.g.
disk crash). Walks every estimate row through the same state-mutation
logic as `SlippageSqrtCalculator._replay_apply` and writes a single
fresh checkpoint. Recomputing aggregates from the journal is
deterministic because each journal row carries both the kernel inputs
and the produced bps numbers.

## 11. Acceptance (per the parent issue)

- Unit tests cover happy path AND at least one failure mode
  (covered: invalid input, sigma=0 corner case, journal-write
  failure, journal-replay-required, corrupted journal line,
  Q/V math, multiple-symbol aggregation, latency budget,
  total_slippage_bps = impact + fee, verdict thresholds).
- Integration smoke: `tests/test_integration_smoke.py` replays
  a synthetic historical estimate journal through the
  `LiveExecutionRunner` and asserts every estimate lands in the
  journal exactly once with consistent per-symbol aggregates after
  restart.

## 12. Constraints (per the parent issue)

- Latency budget: ≤ 250 µs Python overhead per request at p99,
  measured against the **kernel** (Almgren factorisation + verdict
  classification + report build). The full `estimate()` call may
  take longer due to WAL write + `fsync`; the kernel-only cost
  is enforced at `tests/test_kernel.py::TestLatencyBudget`.
- Persistence: every estimate lands in the journal BEFORE the
  estimate is returned; `fsync` follows each write.
- No silent drops: invalid inputs raise `InvalidRequestError`;
  journal failures raise `SlippageSqrtJournalWriteError`;
  corruption raises `SlippageSqrtHalt`; replay without checkpoint
  raises `SlippageSqrtJournalReplayRequired`.
- Folder naming: `_p7exec_027` suffix, never `_v1`/`_v2`.

## 13. Versioning

KERNEL_VERSION = "0.1.0". Breaking changes to `SlippageSqrtRequest`,
`SlippageSqrtEstimate`, verdict names, or the Almgren factorisation
require a new folder suffix and a major version bump.
