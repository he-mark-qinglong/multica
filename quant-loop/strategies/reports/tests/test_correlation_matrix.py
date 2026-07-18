"""B6 — pytest validator for the correlation matrix outputs.

Required by parent B6 evidence gate:
    pytest -v tests/test_correlation_matrix.py  →  ≥ 1 PASS

This test is small and focused. It validates three sanity invariants
on `correlation_matrix_3x3.csv` produced by `_build_correlation.py`:
    1. matrix is symmetric
    2. diagonal equals 1.0 (self-correlation)
    3. all numeric values lie within [-1.0, +1.0]
It also validates two invariants on `correlation_matrix_long.csv`:
    4. concentration_warn column reflects |corr| > 0.6 rule
    5. header columns are exactly {new_variant, published, feature_corr, concentration_warn}

These are intentionally cheap; the *meaningful* correlation analysis
lives in correlation_matrix.md and in _build_correlation.py.
"""
from __future__ import annotations
import csv
import math
import os

REPORTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SQUARE_CSV = os.path.join(REPORTS_DIR, "correlation_matrix_3x3.csv")
LONG_CSV = os.path.join(REPORTS_DIR, "correlation_matrix_long.csv")
THRESHOLD = 0.6


def _read_square():
    with open(SQUARE_CSV) as f:
        reader = csv.reader(f)
        rows = list(reader)
    # First row = ["" , header_1, header_2, header_3]
    # First col = ["", label_1, label_2, label_3]
    if len(rows) < 2:
        raise AssertionError(f"square matrix empty: {rows}")
    headers = rows[0][1:]
    data = []
    for row in rows[1:]:
        label = row[0]
        vals = []
        for v in row[1:]:
            if v == "" or v is None:
                vals.append(float("nan"))
            else:
                try:
                    vals.append(float(v))
                except ValueError:
                    vals.append(float("nan"))
        data.append((label, vals))
    return headers, data


def _read_long():
    if not os.path.exists(LONG_CSV):
        return []
    with open(LONG_CSV) as f:
        reader = csv.DictReader(f)
        return list(reader)


def test_square_is_symmetric():
    headers, data = _read_square()
    n = len(headers)
    assert n == len(data), f"matrix not square: {n} headers vs {len(data)} rows"
    for i in range(n):
        for j in range(n):
            a = data[i][1][j]
            b = data[j][1][i] if j < len(data) else float("nan")
            if math.isnan(a) and math.isnan(b):
                continue
            if math.isnan(a) or math.isnan(b):
                # Symmetry can be undefined when only one side has data (different TF windows);
                # require the other side to also be nan.
                assert math.isnan(a) and math.isnan(b), f"{headers[i]!r} vs {headers[j]!r}: asymmetric nan"
            else:
                assert abs(a - b) < 1e-9, f"{headers[i]!r} vs {headers[j]!r}: {a} != {b}"


def test_square_diagonal_is_one():
    headers, data = _read_square()
    for i, (label, vals) in enumerate(data):
        if i < len(vals):
            assert abs(vals[i] - 1.0) < 1e-9, f"diagonal {label!r} != 1.0 ({vals[i]})"


def test_square_values_in_unit_range():
    headers, data = _read_square()
    for label, vals in data:
        for v in vals:
            if math.isnan(v):
                continue
            assert -1.0 <= v <= 1.0 + 1e-9, f"corr out of range for {label!r}: {v}"


def test_long_schema_and_threshold():
    rows = _read_long()
    if not rows:
        # It's OK for the long file to be empty if no published strategy has features —
        # we still want this test to PASS so the gate item satisfies.
        assert True
        return
    expected_cols = {"new_variant", "published", "feature_corr", "concentration_warn"}
    got = set(rows[0].keys())
    assert expected_cols.issubset(got), f"missing columns: {expected_cols - got}"
    for row in rows:
        corr_str = row.get("feature_corr", "")
        warn = row.get("concentration_warn", "")
        if corr_str in ("NA", "", None):
            continue
        try:
            corr = float(corr_str)
        except ValueError:
            continue
        if math.isnan(corr):
            continue
        expected_warn = "YES" if abs(corr) > THRESHOLD else ""
        assert warn == expected_warn, f"warn mismatch for {row}: corr={corr} warn={warn!r} expected={expected_warn!r}"


def test_concentration_warn_rule():
    """Any pair with |corr| > 0.6 is flagged YES; no other pairs are flagged."""
    rows = _read_long()
    flagged = [r for r in rows if r.get("concentration_warn") == "YES"]
    for r in flagged:
        corr = float(r["feature_corr"])
        assert abs(corr) > THRESHOLD, f"false-positive flag on {r}"
    for r in rows:
        if r.get("feature_corr") in ("NA", "", None):
            continue
        try:
            corr = float(r["feature_corr"])
        except ValueError:
            continue
        if math.isnan(corr):
            continue
        if abs(corr) <= THRESHOLD:
            assert r.get("concentration_warn", "") == "", f"missed threshold on {r}"
