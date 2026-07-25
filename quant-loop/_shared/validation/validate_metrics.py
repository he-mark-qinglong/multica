"""Validate metrics.json against the 9-key schema + provenance fields.

Single source of truth for the schema is ``compute_metrics.py`` (return dict
literal at :108-117). This validator mirrors those keys + types so any caller
emitting metrics through :func:`_shared.validation.compute_metrics.compute_metrics`
is guaranteed to pass.

Usage:
    from _shared.validation.validate_metrics import (
        validate_metrics, check_provenance,
    )
    violations = validate_metrics(payload)
    warnings = check_provenance(payload)

CLI:
    python3 -m _shared.validation.validate_metrics <path> [--report]
        [--strict-provenance]
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any, Callable, Iterable

# ---------------------------------------------------------------------------
# 9-key schema (mirrored verbatim from compute_metrics.py:108-117).
# ---------------------------------------------------------------------------
REQUIRED_KEYS: dict[str, type] = {
    "sharpe_daily": float,
    "annualized_return": float,
    "max_drawdown_pct": float,
    "profit_factor": float,
    "n_trades": int,
    "n_bars": int,
    "win_rate": float,
    "calmar": float,
    "sortino": float,
}

# Provenance fields every *new* metrics.json must carry. Historical files
# written before this schema shipped are exempt (warnings only).
PROVENANCE_KEYS: tuple[str, ...] = (
    "strategy",
    "cost_bps_rt",
    "data_window",
    "generated_at",
)

# Domain constraints per compute_metrics.py semantics.
_DOMAIN_CHECKS: dict[str, Callable[[float | int], bool]] = {
    "win_rate": lambda v: 0.0 <= v <= 1.0,
    "max_drawdown_pct": lambda v: -1.0 <= v <= 0.0,
    "n_trades": lambda v: v >= 0,
    "n_bars": lambda v: v >= 0,
}


def _is_real_number(v: Any) -> bool:
    """True for finite int/float (excludes bool, str, None, NaN, Inf)."""
    if isinstance(v, bool):
        return False
    if not isinstance(v, (int, float)):
        return False
    return math.isfinite(float(v))


def _is_real_int(v: Any) -> bool:
    """True for a non-bool finite int."""
    if isinstance(v, bool):
        return False
    if not isinstance(v, int):
        return False
    return True


def _check_key(key: str, expected_type: type, value: Any) -> list[str]:
    """Validate a single key against its expected type + domain constraint."""
    out: list[str] = []
    # Type check (with the bool/finite carve-outs).
    if expected_type is int:
        if not _is_real_int(value):
            out.append(f"key '{key}': expected int, got {type(value).__name__}")
            return out
    else:  # float
        if not _is_real_number(value):
            out.append(
                f"key '{key}': expected finite number, "
                f"got {type(value).__name__}"
            )
            return out

    domain = _DOMAIN_CHECKS.get(key)
    if domain is not None:
        try:
            ok = bool(domain(float(value) if expected_type is float else value))
        except (TypeError, ValueError):
            return out  # type already flagged above
        if not ok:
            out.append(f"key '{key}': value {value!r} violates domain constraint")
    return out


def validate_metrics(payload: Any) -> list[str]:
    """Return schema violation strings; empty list means PASS.

    Checks:
      * ``payload`` is a dict.
      * every key in :data:`REQUIRED_KEYS` is present.
      * each value has the right type and a finite value.
      * each value satisfies its domain constraint (e.g. ``win_rate ∈ [0,1]``).
    """
    out: list[str] = []
    if not isinstance(payload, dict):
        return [f"payload must be a dict, got {type(payload).__name__}"]

    missing = [k for k in REQUIRED_KEYS if k not in payload]
    for k in missing:
        out.append(f"missing key: {k}")
    if missing:
        # If keys are missing, no point type-checking them; the type rules
        # below still catch any extra fields with bad types.
        pass

    for key, expected_type in REQUIRED_KEYS.items():
        if key not in payload:
            continue
        out.extend(_check_key(key, expected_type, payload[key]))
    return out


def check_provenance(
    payload: Any, keys: Iterable[str] = PROVENANCE_KEYS
) -> list[str]:
    """Return WARN-grade strings for missing provenance fields.

    Provenance is advisory only — missing provenance does NOT count as a
    schema violation. ``validate_metrics`` returns the violation list;
    ``check_provenance`` returns the warning list separately so callers can
    decide whether to escalate (e.g. ``--strict-provenance`` in the CLI).
    """
    out: list[str] = []
    if not isinstance(payload, dict):
        return out  # nothing to provenance-check on a non-dict
    for k in keys:
        if k not in payload or payload[k] in (None, "", []):
            out.append(f"missing provenance: {k}")
    return out


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _load_payload(path: Path) -> tuple[int, dict[str, Any] | None, str | None]:
    """Return (exit_code, payload_or_None, error_message_or_None).

    Exit codes:
      0 = loaded OK.
      2 = file missing or unreadable / JSON parse failure.
    """
    if not path.exists():
        return 2, None, f"file not found: {path}"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        return 2, None, f"read error: {e}"
    try:
        return 0, json.loads(text), None
    except json.JSONDecodeError as e:
        return 2, None, f"json parse error: {e}"


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. See module docstring + ``--help`` for usage."""
    parser = argparse.ArgumentParser(
        description=(
            "Validate a metrics.json file against the 9-key schema "
            "(source: _shared.validation.compute_metrics.compute_metrics)."
        ),
    )
    parser.add_argument("path", help="path to a metrics.json file")
    parser.add_argument(
        "--report",
        action="store_true",
        help="print per-line OK/WARN/FAIL results plus a summary line",
    )
    parser.add_argument(
        "--strict-provenance",
        action="store_true",
        help="promote provenance-missing from WARN to FAIL (exit 1)",
    )
    args = parser.parse_args(argv)

    path = Path(args.path)
    code, payload, err = _load_payload(path)
    if err is not None:
        print(err, file=sys.stderr)
        return code  # 2

    violations = validate_metrics(payload)
    warnings = check_provenance(payload)

    if args.report:
        if not violations and not warnings:
            print("OK: 9-key schema + provenance complete")
        else:
            for v in violations:
                print(f"FAIL: {v}")
            for w in warnings:
                print(f"WARN: {w}")
        if violations:
            print(
                f"FAIL ({len(violations)} violations, {len(warnings)} warnings)",
                file=sys.stderr,
            )
        elif warnings:
            print(
                f"PASS ({len(warnings)} warnings)", file=sys.stderr
            )
        else:
            print("PASS (0 warnings)", file=sys.stderr)

    # Decision policy:
    # - schema violations -> exit 1
    # - provenance warnings do NOT fail unless --strict-provenance
    # - no violations + (no warnings OR not strict) -> exit 0
    # - no violations + warnings + strict -> exit 1
    if violations:
        return 1
    if warnings and args.strict_provenance:
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())


__all__ = [
    "REQUIRED_KEYS",
    "PROVENANCE_KEYS",
    "validate_metrics",
    "check_provenance",
    "main",
]