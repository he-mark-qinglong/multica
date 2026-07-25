import { describe, it, expect } from "vitest";
import type { RunMetric } from "@multica/core/types";
import { readVerdict } from "./verdict";

/** Minimal RunMetric factory — every numeric column defaults to null so tests
 *  only have to override the fields under test. Mirrors the makeIssue pattern
 *  in packages/views/issues/utils/filter.test.ts. */
function makeMetric(overrides: Partial<RunMetric> = {}): RunMetric {
  return {
    id: "m-1",
    task_id: null,
    issue_id: null,
    artifact_id: null,
    campaign: null,
    iteration: null,
    sharpe: null,
    sortino: null,
    calmar: null,
    ann_return: null,
    max_drawdown: null,
    profit_factor: null,
    oos_sharpe: null,
    oos_windows: null,
    timeframe: null,
    symbols: null,
    params: null,
    extra: null,
    created_at: null,
    gate_status: null,
    gate_detail: null,
    ...overrides,
  };
}

const DEFAULTS = { verdict: null, killReason: null, killed: false };

describe("readVerdict", () => {
  it("returns defaults when extra is null", () => {
    expect(readVerdict(makeMetric({ extra: null }))).toEqual(DEFAULTS);
  });

  it("returns defaults when extra is an empty object", () => {
    expect(readVerdict(makeMetric({ extra: {} }))).toEqual(DEFAULTS);
  });

  it("returns defaults when extra is undefined (key missing)", () => {
    // extra is the field itself; "missing keys" is simulated by reading from
    // an object that simply has no verdict / kill_reason / divergence_flag.
    expect(readVerdict(makeMetric({ extra: { unrelated: "noise" } }))).toEqual(DEFAULTS);
  });

  it("reads a non-empty verdict verbatim", () => {
    const m = makeMetric({ extra: { verdict: "CV_PASS" } });
    expect(readVerdict(m)).toEqual({ verdict: "CV_PASS", killReason: null, killed: false });
  });

  it("reads a non-empty kill_reason and marks killed=true", () => {
    const m = makeMetric({ extra: { kill_reason: "framework CV sharpe -4.86" } });
    expect(readVerdict(m)).toEqual({
      verdict: null,
      killReason: "framework CV sharpe -4.86",
      killed: true,
    });
  });

  it("treats a whitespace-only kill_reason as missing (not killed)", () => {
    const m = makeMetric({ extra: { kill_reason: "  " } });
    expect(readVerdict(m)).toEqual({ verdict: null, killReason: null, killed: false });
  });

  it("marks killed=true when divergence_flag is KILLED (and no kill_reason)", () => {
    const m = makeMetric({ extra: { divergence_flag: "KILLED" } });
    expect(readVerdict(m)).toEqual({ verdict: null, killReason: null, killed: true });
  });

  it("marks killed=true when divergence_flag is rejected (case-insensitive)", () => {
    const m = makeMetric({ extra: { divergence_flag: "rejected" } });
    expect(readVerdict(m)).toEqual({ verdict: null, killReason: null, killed: true });
  });

  it("does NOT mark killed for W5_FAIL_FEE_SHOCK (only KILLED/REJECTED qualify)", () => {
    const m = makeMetric({ extra: { divergence_flag: "W5_FAIL_FEE_SHOCK" } });
    expect(readVerdict(m)).toEqual(DEFAULTS);
  });

  it("does NOT mark killed for OK", () => {
    const m = makeMetric({ extra: { divergence_flag: "OK" } });
    expect(readVerdict(m)).toEqual(DEFAULTS);
  });

  it("coerces wrong-typed verdict to null and never throws", () => {
    expect(readVerdict(makeMetric({ extra: { verdict: 42 } }))).toEqual(DEFAULTS);
  });

  it("coerces wrong-typed kill_reason to null and never throws", () => {
    expect(readVerdict(makeMetric({ extra: { kill_reason: { x: 1 } } }))).toEqual(DEFAULTS);
  });

  it("coerces wrong-typed divergence_flag to null and never throws", () => {
    expect(readVerdict(makeMetric({ extra: { divergence_flag: ["KILLED"] } }))).toEqual(DEFAULTS);
  });

  it("trims surrounding whitespace from verdict", () => {
    const m = makeMetric({ extra: { verdict: "  CV_PASS  " } });
    expect(readVerdict(m).verdict).toBe("CV_PASS");
  });

  it("trims surrounding whitespace from kill_reason and still marks killed", () => {
    const m = makeMetric({ extra: { kill_reason: "  sharpe -4.86  " } });
    const v = readVerdict(m);
    expect(v.killReason).toBe("sharpe -4.86");
    expect(v.killed).toBe(true);
  });
});
