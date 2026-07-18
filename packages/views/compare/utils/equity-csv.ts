/**
 * Liberal equity-curve CSV parsing for the Compare page overlay.
 *
 * Backtest runners don't share a strict schema, so the parser follows the
 * platform convention: first row is a header; the timestamp column is the
 * first header containing "time" or "date", the equity column the first
 * containing "equity", "balance", or "value". Anything unparseable returns
 * null — the page renders a per-series warning instead of crashing.
 */

/** Extracts the equity series (in row order) from CSV text, or null when
 *  no usable equity column / too few parseable points. */
export function parseEquityCsv(text: string): number[] | null {
  const lines = text.split(/\r?\n/).filter((l) => l.trim().length > 0);
  if (lines.length < 2) return null;

  const header = (lines[0] ?? "").split(",").map((h) => h.trim().toLowerCase());
  const equityIdx = header.findIndex((h) => /equity|balance|value/.test(h));
  if (equityIdx === -1) return null;

  const values: number[] = [];
  for (let i = 1; i < lines.length; i++) {
    const cells = (lines[i] ?? "").split(",");
    const v = Number.parseFloat(cells[equityIdx]?.trim() ?? "");
    if (Number.isFinite(v)) values.push(v);
  }
  return values.length >= 2 ? values : null;
}

/** Caps a series at `maxPoints` via even stride so long backtests stay
 *  cheap to render. Always keeps the first and last point. */
export function downsample(values: number[], maxPoints = 500): number[] {
  if (values.length <= maxPoints) return values;
  const stride = values.length / maxPoints;
  const out: number[] = [];
  for (let i = 0; i < maxPoints; i++) {
    const v = values[Math.floor(i * stride)];
    if (v !== undefined) out.push(v);
  }
  const last = values[values.length - 1];
  if (last !== undefined) out.push(last);
  return out;
}
