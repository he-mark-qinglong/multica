/**
 * Verdict / kill-status readers for the Compare page.
 *
 * The publisher writes optional `verdict` / `kill_reason` / `kill_evidence`
 * string keys into the metric blob; the server passes unknown keys through
 * into `RunMetric.extra`. Older rows lack them — every read coerces to a
 * safe default, never throws.
 */

import type { RunMetric } from "@multica/core/types";

export interface Verdict {
  /** One-line publisher verdict (e.g. "CV_PASS", "KILL"), or null. */
  verdict: string | null;
  /** Why the strategy was killed, or null. */
  killReason: string | null;
  /** True when kill_reason is non-empty OR divergence_flag is KILLED/REJECTED. */
  killed: boolean;
}

const KILL_FLAGS = new Set(["KILLED", "REJECTED"]);

function readString(v: unknown): string | null {
  if (typeof v !== "string") return null;
  const s = v.trim();
  return s.length > 0 ? s : null;
}

export function readVerdict(m: RunMetric): Verdict {
  const ex = m.extra ?? {};
  const killReason = readString(ex.kill_reason);
  const flag = readString(ex.divergence_flag);
  return {
    verdict: readString(ex.verdict),
    killReason,
    killed: killReason !== null || (flag !== null && KILL_FLAGS.has(flag.toUpperCase())),
  };
}