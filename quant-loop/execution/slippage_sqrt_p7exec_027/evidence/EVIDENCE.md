# EVIDENCE — `slippage_sqrt_p7exec_027`

> Per [SMA-36214](mention://issue/e9632760-b568-490b-964c-d361259fe203) acceptance: unit tests + integration smoke + bench.

## Verdict

```
PASS — slippage_sqrt ships. All parent-issue acceptance gates met:

  ✓ Unit tests: 40/40 (test_kernel.py)
  ✓ Runner wrapper tests: 12/12 (test_runner.py)
  ✓ Integration smoke vs LiveExecutionRunner: 7/7 (test_integration_smoke.py)
  ✓ Kernel p99 latency: 10.35 µs (budget 250 µs) — ~24× headroom
  ✓ WAL persistence: every estimate journaled, fsynced, rehydrates cleanly
  ✓ No-silent-drop: journal failures raise SlippageSqrtJournalWriteError,
    invalid input raises InvalidRequestError, replay-without-checkpoint
    raises SlippageSqrtJournalReplayRequired
  ✓ Folder suffix `_p7exec_027`, no `_v1` / `_v2`
```

## What ships

```
~/multica/quant-loop/execution/
├── runner.py                                          (P7-EXEC-027 wire-up)
└── slippage_sqrt_p7exec_027/
    ├── README.md                                      (purpose + folder layout)
    ├── SPEC.md                                        (full extended spec)
    ├── INTERFACE_CONTRACT.md                          (wire contract)
    ├── exceptions.py                                  (SlippageSqrtError tree)
    ├── models.py                                      (SlippageSqrtRequest/Estimate, constants)
    ├── journal.py                                     (WAL + checkpoint + fsync)
    ├── kernel.py                                      (Almgren sqrt math + verdict + Calculator)
    ├── runner.py                                      (component-level ExecutionRunner wrapper)
    ├── rebuild_checkpoint.py                          (CLI to rebuild state.json from journal)
    ├── bench_harness.py                               (micro-bench — captures evidence/bench.json)
    ├── tests/
    │   ├── test_kernel.py                             (40 tests)
    │   ├── test_runner.py                             (12 tests)
    │   └── test_integration_smoke.py                  (7 tests)
    └── evidence/
        ├── EVIDENCE.md                                (this file)
        └── bench.json                                 (micro-bench JSON)
```

## Kernel — Almgren (2003) square-root factorisation

```
V_per_s        = daily_volume / seconds_per_day
participation  = qty / (V_per_s * arrival_horizon_s)
temp_impact_bps = k_factor * volatility_per_s * sqrt(participation) * 10000
total_bps       = temp_impact_bps + fee_bps
```

Classification (adverse-positive bps): `minimal < 5.0 < low < 15.0 < moderate < 50.0 < high < 200.0 ≤ extreme`.

## Test results

```
$ cd ~/multica/quant-loop/execution \
    && python3 -m unittest -v \
       slippage_sqrt_p7exec_027.tests.test_kernel \
       slippage_sqrt_p7exec_027.tests.test_runner \
       slippage_sqrt_p7exec_027.tests.test_integration_smoke

Ran 59 tests in 0.401s

OK
```

Full output:

| Suite | # tests | # pass | # fail |
|-------|--------:|-------:|-------:|
| `test_kernel` (Almgren math + validation + verdict + latency + rebuild) | 40 | 40 | 0 |
| `test_runner` (ExecutionRunner wrapper stats/shutdown/idempotency) | 12 | 12 | 0 |
| `test_integration_smoke` (LiveExecutionRunner wire-up + restart + cold-start) | 7 | 7 | 0 |

### Test coverage themes

- Happy path: Almgren factorisation against analytic oracle for several
  qty / volatility / daily_volume / horizon / k_factor combinations.
- Verdict buckets: minimal / low / moderate / high / extreme each pinned.
- Edge cases parent issue names: `volatility_per_s == 0` (sigma=0 → 0
  impact + minimal verdict), `qty == 0` rejected, `daily_volume == 0`
  rejected, `k_factor <= 0` rejected, negative `fee_bps` rejected.
- total_slippage_bps = temporary_impact_bps + fee_bps (additive identity).
- Validation: empty strings, bad side, negative numbers, non-positive
  quantities, crossed strings.
- WAL: write-rotate close-reopen, replay → same per-symbol aggregates.
- Wallet durability: `state.json` checkpoint round-trips.
- Latency budget: kernel p99 < 250 µs over 3 × 5,000 requests.
- Runner wrapper: idempotency, shutdown semantics, kernel_version pinning.
- Wire-up: every estimate journaled exactly once; restart rehydrates;
  no silent bridge from `ingest_fill` to slippage_sqrt.

## Hot-path bench

```
$ python3 slippage_sqrt_p7exec_027/bench_harness.py

--- slippage_sqrt hot-path bench ---
  kernel-only: worst p99 across 3 rounds = 10.35 us (budget 250 us → PASS)
    round 0: p50=6.02us p95=6.41us p99=10.35us max=131.05us
    round 1: p50=5.99us p95=6.20us p99=8.12us max=46.89us
    round 2: p50=5.97us p95=6.19us p99=6.57us max=40.84us
  full-estimate (kernel + WAL write + fsync) over 200 reqs:
    p50=957.32us p95=1327.08us p99=1473.04us max=1533.68us

EVIDENCE written to .../evidence/bench.json
```

- Kernel-only p99: **10.35 µs** (budget 250 µs ⇒ 24× headroom).
- Full `estimate()` p99: **1473 µs** (I/O-dominated: WAL write +
  `os.fsync` per request, on tmpfs). The 250 µs budget applies to the
  kernel only; SPEC.md §4 documents the I/O cost separately and
  notes a Rust PyO3 offload is tracked as a follow-up if the budget
  later tightens.

## Issues found and fixed during validation

- **`tests/test_kernel.py` — verdict-classification tests had wrong
  qty fixtures.** The original author wrote fixture qty values (8,
  80, 600, 5000 BTC) and inline claims like "8 BTC → ~5.95 bps"
  without re-deriving. For default params
  (`v_per_s = 100000/86400 ≈ 1.1574`) the formula gives
  `impact_bps ≈ sqrt(qty / v_per_s)`, which yields `qty=8 → ~2.6 bps`,
  well below the 5.0 bps "low" threshold. Updated to qty values (30,
  300, 3000, 50000) that actually land in the asserted verdict
  ranges; added boundary-pinning assertions. **Kernel math itself was
  correct**; only the test fixtures were wrong.
- **`tests/test_integration_smoke.py` — same root cause** for
  `_build_requests()`. `_req("w:btc:2", "BTCUSDT", qty=10.0)` had
  impact ≈ 2.94 bps (minimal), not "low"; `_req("w:sol:1", "SOLUSDT",
  qty=200.0)` had impact ≈ 13.15 bps (low), not "moderate". Updated
  to qty=100 (low) and qty=300 (moderate).
- **`tests/test_integration_smoke.py::test_no_silent_bridge_from_ingest_fill`
  imported `FillRecord` from `strategy_execution_scorer_p7exec_091`
  which is not on this branch.** Added a try/except stub that
  constructs a minimal local `FillRecord` dataclass when the sibling
  module is unavailable, preserving the test's semantic ("ingest_fill
  must NOT silently fan out to slippage_sqrt").
- **`execution/runner.py` — did not exist on this branch.** Wrote a
  minimal `LiveExecutionRunner` that wires the slippage_sqrt
  calculator into the canonical runner surface (ingest_slippage_sqrt_request,
  slippage_sqrt_stats, stats, shutdown) with an explicit no-bridge
  `ingest_fill` stub. The full sibling wire-up (anomaly detector,
  attribution backend, scorer, venue-fill-rate, fill-report
  normalizer, cross-asset, event-horizon) is shipped on the
  `agent/multica-code/sma-35145-per-bar-compounding` branch and is
  out of scope for P7-EXEC-027 (copying it would have pulled in seven
  additional components not covered by this ticket).

## How to reproduce

```bash
cd ~/multica/quant-loop/execution

# Unit + wire-up tests
python3 -m unittest -v \
    slippage_sqrt_p7exec_027.tests.test_kernel \
    slippage_sqrt_p7exec_027.tests.test_runner \
    slippage_sqrt_p7exec_027.tests.test_integration_smoke

# Hot-path bench → evidence/bench.json
python3 slippage_sqrt_p7exec_027/bench_harness.py

# Replay-only rebuild (when journal exists but state.json is lost)
python3 -m slippage_sqrt_p7exec_027.rebuild_checkpoint <journal_dir>
```

## Provenance

- Adopted from a peer worktree's pre-existing implementation
  (`agent/multica-code/sma-35145-per-bar-compounding` branch,
  uncommitted as of 2026-07-25) — full SPEC + interface + tests
  + integration smoke structure intact.
- Validated by `multica-strategy` (L3 verification/execution owner,
  [SMA-36214](mention://issue/e9632760-b568-490b-964c-d361259fe203)):
  found and fixed test-fixture math (described above); ran full suite
  end-to-end; produced this evidence.
