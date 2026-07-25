# VERDICT — signal-enhance-h3 full-history validation (W4-T15, 2026-07-25)

**Evidence summary:** 7-window OOS mean daily-resampled Sharpe **9.2073** vs H3 baseline **1.8748**; bootstrap CI95 = **[7.7901, 11.0367]** (seed 42 / 10000); 60bps pair-RT fee-shock Sharpe **10.4141** vs baseline **-0.0213**; KEEP/KILL verdict is reserved for the research main line.

> KEEP/KILL verdict intentionally omitted (per task card §T15). The research main line owns the KEEP/KILL call against the SPEC's falsification conditions; this document only supplies the evidence.

## 1. Per-window table (se_h3 locked enhancement vs H3 baseline)

| win | test_start → test_end | se_h3 Sharpe | baseline Sharpe | se_h3 n_trades | baseline n_trades | se_h3 MDD | se_h3 PF |
|---:|:---|---:|---:|---:|---:|---:|---:|
| 0 | 2022-11-20 16:01:00 → 2023-05-22 04:00:00 | 7.1906 | 1.7252 | 358 | 4434 | -6.86% | 1.0944 |
| 1 | 2023-05-22 04:01:00 → 2023-11-20 16:00:00 | 6.9913 | 1.5964 | 367 | 4543 | -5.01% | 1.0940 |
| 2 | 2023-11-20 16:01:00 → 2024-05-21 04:00:00 | 8.0745 | 1.0471 | 339 | 4182 | -4.37% | 1.0947 |
| 3 | 2024-05-21 04:01:00 → 2024-11-19 16:00:00 | 14.1022 | 3.0774 | 850 | 4317 | -1.97% | 1.1094 |
| 4 | 2024-11-19 16:01:00 → 2025-05-21 04:00:00 | 9.8605 | 1.6879 | 379 | 4341 | -2.81% | 1.1133 |
| 5 | 2025-05-21 04:01:00 → 2025-11-19 16:00:00 | 8.6439 | -0.3798 | 373 | 4194 | -2.02% | 1.0764 |
| 6 | 2025-11-19 16:01:00 → 2026-05-21 04:00:00 | 9.5879 | 4.3692 | 370 | 4321 | -1.91% | 1.1019 |

## 2. Aggregate table

| metric | se_h3 | H3 baseline |
|:---|---:|---:|
| OOS mean Sharpe (daily-resampled) | 9.2073 | 1.8748 |
| OOS bootstrap CI95 lower | 7.7901 | 0.8879 |
| OOS bootstrap CI95 upper | 11.0367 | 2.9363 |
| OOS worst MDD | -6.86% | -13.30% |
| OOS mean PF | 1.0978 | 1.0122 |
| OOS mean annualized return | 163.80% | 31.79% |
| total trades (7 windows) | 3036 | 30332 |
| full-history Sharpe (daily-resampled) | 10.7653 | 1.4683 |
| full-history MDD | -5.48% | -16.26% |
| full-history PF | 1.0934 | 1.0097 |

## 3. Fee-shock table (pair round-trip)

| cost tier | se_h3 Sharpe | H3 baseline Sharpe |
|:---|---:|---:|
| inhouse 4 bps | 10.7419 | 1.3683 |
| freqtrade 24 bps | 10.6250 | 0.8699 |
| backtrader 60 bps | 10.4141 | -0.0213 |

## 4. SPEC falsification conditions (verbatim from `SPEC_signal_enhance_h3_fullhist.md`)

| # | condition (verbatim) | threshold | observed | verdict |
|---:|:---|:---|---:|:---:|
| 1 | 7 窗 OOS mean Sharpe（daily-resampled）< 1.0 | 1.0 | 9.2073 | FALSE |
| 2 | bootstrap CI lower（seed=42，resamples=10000）< 0.5 | 0.5 | 7.7901 | FALSE |
| 3 | 60 bps pair-RT fee-shock Sharpe ≤ 0 | 0 | 10.4141 | FALSE |
| 4 | parity 测试（T05/T06）不通过 | n/a | not applicable (T05/T06 already in_review per upstream) | n/a |

## 5. Gate result (G1-G7 + T1)

Run via `certify_metrics` imported from `_shared.gates.enforce`. G5/G7 are deliberately NOT_RUN by design (CPCV + DSR live in downstream workstreams); raw enforce.py reasons are preserved in `se_h3_metrics.json` for provenance.

| gate | criterion | observed | status |
|:---:|:---|---:|:---:|
| G1 | Sharpe ≥ 1.0 | 9.207284784457107 | **PASS** |
| G2 | Annualized return ≥ 0.15 | 1.637983784959825 | **PASS** |
| G3 | Max drawdown > -25% | -0.0686424495606931 | **PASS** |
| G4 | Profit factor > 1.5 | 1.0977589234267735 | **FAIL** |
| G5 | CPCV mean OOS Sharpe ≥ 1.0 | n/a | **NOT_RUN** |
| G6 | Bootstrap CI95 lower ≥ 0.5 | 7.7901459965406135 | **PASS** |
| G7 | Deflated Sharpe Ratio > 0 | n/a | **NOT_RUN** |
| T1 | n_trades ≥ 30 | 3036 | **PASS** |

## 6. Environment

7 windows were computed across two environments (Mac vs .105). All boundary assertions locked against H3-baseline-repro/metrics.json walk_forward_oos.per_window ISO timestamps PASSED on the executing host. Mean Sharpe is an arithmetic average of 7 per-window daily-resampled Sharpes (numerically identical across environments to the limits of float64).

- Windows 0-3: Mac, `/Users/mark/sdk/mamba-envs/trading/bin/python3` (pandas 2.2.3, NumPy 2.2.6).
- Windows 4-6: server-105 (`smark@192.168.0.105`), `/usr/bin/python3` (pandas 3.0.3, NumPy 2.4.6, Python 3.12.3). Per-window `se_h3_wf_window_{2,4,5,6}.env.txt` carries script/common/loop/signals.py md5 + data file sizes for win6 × baseline md5 cross-validation, per task-card §0.1 risk mitigation.
- Windows 0, 1, 3 do not have an `.env.txt` (Mac runs pre-dated the §0.1 env.txt protocol; the per-window boundary assertion `test_start_iso` still locked against `H3-baseline-repro/metrics.json walk_forward_oos.per_window` and PASSED on every window).

## 7. Cross-cuts (raw enforce.py reasons)

```
- G4 profit_factor > 1.5: got '?', expected Profit factor > 1.5
- G5 cpcv_mean_oos_sharpe >= 1.0: MISSING_FIELD:cpcv_mean_oos_sharpe
- G7 deflated_sharpe > 0.0: MISSING_FIELD:deflated_sharpe
```

## 8. KEEP/KILL verdict

**Deferred to the research main line.** This evidence pack is deterministic aggregation only; it intentionally does not emit a KEEP/KILL call against the SPEC's falsification conditions.
