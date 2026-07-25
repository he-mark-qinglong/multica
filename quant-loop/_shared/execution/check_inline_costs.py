"""Inline-cost scanner — read-only drift detector for quant-loop strategies.

Reads every Python file under ``strategies/`` (configurable via ``--root``),
flags two patterns of hard-coded execution-cost literals that belong inside
``backtest/factor_backtester.py`` and ``_shared/execution/cost_model.py``:

  1. ``COST_LITERAL_RE`` — fraction-of-notional literals like ``0.0004``,
     ``0.0008``, ``0.0011``, ``0.0016``, ``0.0022``, ``0.0024`` which encode
     4/8/11/16/22/24 bps fractions that should be derived from ``apply_cost``
     against the ``BINANCE_FUTURES`` venue. Negative lookarounds keep
     close-but-unrelated tokens out: ``0.00225`` (extra trailing digit),
     ``1.0004`` (multiplier inside a larger coefficient), etc.
  2. ``ASSIGN_RE`` — explicit assignment to a name containing
     fee/slippage/cost-bps (e.g. ``fee_bps = 5.0``,
     ``FEE_RT_BPS = 22.0``, ``slippage_bps_per_side = 7``).

The scanner is **read-only**. Migration of flagged literals into the
approved ``apply_cost(venue=BINANCE_FUTURES)`` path is owned by the
follow-up migration tasks (T7 / T8 / T14). Do not let the scanner rewrite
strategies on its own.

CLI
---
``--report`` (default when no flag given) prints every violation plus a
summary line, exit 0. ``--enforce`` exits 1 if any violation is found,
0 otherwise. ``--root DIR`` overrides the default ``strategies/`` root
(for tests + repo-wide dry runs).

Whitelist
---------
Exempt paths never appear in the output:

  * ``strategies/_graveyard/`` — frozen archive; historical drift is the
    whole point of keeping the directory.
  * anything matching ``framework_adapter_*.py`` under ``strategies/`` —
    adapter files are owned by the framework-adapter workflow (different
    review chain, never smark).
  * test files: ``test_*.py``, ``*_test.py``, or any path segment
    ``tests/`` — expected values are legal.
  * canonical source files: ``backtest/factor_backtester.py`` and
    ``_shared/execution/cost_model.py`` (the regression would re-flag the
    constants themselves). Harmless because the default ``--root`` skips
    them, but explicitly whitelisted for the ``--root backtest`` form.
  * harness files (``validation/generic_harness.py``,
    ``validation/oos_harness.py``) — out of scope for strategy migration.

Tests live in ``test_check_inline_costs.py`` in this same directory.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Iterable, Iterator, List, NamedTuple, Tuple


HERE = Path(__file__).resolve().parent
# Repo root for quant-loop work: one directory above _shared/execution/.
# The default scan target is strategies/, the canonical-source whitelist
# references backtest/ and validation/ files at this same level.
REPO_ROOT = HERE.parents[1]
DEFAULT_ROOT = REPO_ROOT / "strategies"


# --- Regexes -----------------------------------------------------------

# Fraction-of-notional bps literals: 4/8/11/16/22/24 bps as `0.00NN`.
# Lookbehind `(?<![\w.])` rejects digits/dots/letters immediately before,
# so `1.0004` (part of a multiplier), `2*0.0004` (preceded by literal
# `*`) -- wait `*` is not in `[\w.]`. Re-tuned: also reject any other
# literal form by requiring the chars immediately before are `start` or
# whitespace or punctuation NOT being `.` (already in the class).
# Lookahead `(?!\d)` rejects extra digits, so `0.00225` (22.5 bps) is
# not flagged as a 22bps literal.
COST_LITERAL_RE = re.compile(
    r"(?<![\w.])0\.00(?:04|08|11|16|22|24)(?!\d)"
)

# Explicit assignment to a cost-naming variable. The pattern matches the
# LHS name (mixed-case fee-bps family, captured in a single alternation
# with word-boundary anchors) followed by `=` and a numeric literal
# anywhere on the same line. We don't try to be clever about `float(...)`
# wrappers vs direct literals -- if the line carries a cost-naming
# assignment and a numeric literal anywhere, the cost number is in scope.
# The spec carves out `coffee_bps` and the like via the `\b` anchor
# (no boundary inside `coffee_bps`).
ASSIGN_RE = re.compile(
    r"\b("
    r"fee_bps|fees_bps(?:_per_(?:side|fill))?|slippage_bps(?:_per_side)?"
    r"|cost_bps_rt"
    r"|[A-Z_]*FEE[A-Z_]*BPS[A-Z_]*"
    r")\s*=\s*[^=\n]*?(?P<value>\d+(?:\.\d+)?)"
)


class Violation(NamedTuple):
    path: Path
    lineno: int
    line: str
    pattern: str  # one of "literal" or "assign"
    matched: str  # the specific matched text (substring of `line`)

    def render(self) -> str:
        excerpt = self.line.rstrip("\n")[:120]
        return (
            f"{self.path}:{self.lineno}: matched {self.pattern} "
            f"`{self.matched}` | {excerpt}"
        )


# --- Whitelist ---------------------------------------------------------

# Path-segment-based exemptions; checked against every path component.
_GRAVEYARD_SEGMENT = "_graveyard"
_FRAMEWORK_ADAPTER_PREFIX = "framework_adapter_"
_TEST_DIR_SEGMENT = "tests"

# Filename patterns for test files (the bare-file convention).
_TEST_FILE_GLOBS = (
    "test_*.py",
    "*_test.py",
)

# Absolute path exemptions (canonical sources whose constants ARE the
# ratified values; re-flaging them would create a noise loop).
_CANONICAL_SOURCES = (
    REPO_ROOT / "backtest" / "factor_backtester.py",
    REPO_ROOT / "_shared" / "execution" / "cost_model.py",
    REPO_ROOT / "validation" / "generic_harness.py",
    REPO_ROOT / "validation" / "oos_harness.py",
)

# TODO List of fee-shock assumptions (`60bps`) are NOT exempted here.
# Per COST_CONVENTION.md rule 2, fee-shock multipliers must be expressed
# against the approved constant with an explanatory comment that names
# the canonical source. The scanner will flag a bare `60` next to fee /
# slippage names; that's intentional -- the author has to either justify
# it in prose or route through the canonical constant.


def _is_test_file(path: Path) -> bool:
    name = path.name
    return any(
        _matches_glob(name, glob) for glob in _TEST_FILE_GLOBS
    )


def _matches_glob(name: str, glob: str) -> bool:
    # Tiny glob helper: support `*` only (the project's actual usage uses
    # `prefix*.py` / `*.py` patterns).
    if "*" not in glob:
        return name == glob
    head, _, tail = glob.partition("*")
    if not name.startswith(head):
        return False
    if tail and not name.endswith(tail):
        return False
    return True


def is_exempt(path: Path) -> bool:
    """Return True if `path` is exempt from the inline-cost scan.

    The exemption set is the union of:

      * canonical-source paths (`backtest/factor_backtester.py`,
        `_shared/execution/cost_model.py`, validation harnesses);
      * any path whose components contain `_graveyard` (frozen archive);
      * any file whose basename matches `test_*.py` or `*_test.py`;
      * any path with a `tests/` component anywhere;
      * any file whose basename starts with `framework_adapter_`.

    The check is component-wise so `/a/strategies/_graveyard/x/y.py` is
    exempt; `/a/_graveyard/x.py` outside ``strategies/`` is also exempt
    (defensive, in case someone symlinks or runs with a wider ``--root``).
    """
    path = path.resolve()
    # Canonical sources — absolute-path match.
    for canonical in _CANONICAL_SOURCES:
        try:
            if path.resolve() == canonical.resolve():
                return True
        except OSError:
            continue

    parts = path.parts

    # Any `tests` segment, or any `_graveyard` segment.
    if _GRAVEYARD_SEGMENT in parts or _TEST_DIR_SEGMENT in parts:
        return True

    # Filename-based exemptions.
    name = path.name
    if name.startswith(_FRAMEWORK_ADAPTER_PREFIX):
        return True
    if _is_test_file(path):
        return True

    return False


# --- Walking -----------------------------------------------------------


def iter_targets(root: Path) -> Iterator[Path]:
    """Yield every ``*.py`` file under ``root`` that passes ``is_exempt``.

    Iterates deterministically (sorted path strings) so repeated runs and
    tests produce stable output.
    """
    root = Path(root).resolve()
    if not root.exists():
        return
    for path in sorted(root.rglob("*.py")):
        if path.is_file() and not is_exempt(path):
            yield path


# --- Line classification ----------------------------------------------


def _is_pure_comment_or_docstring(line: str, in_block: List[bool]) -> bool:
    """Return True if the line is purely a comment after stripping.

    `in_block` is a one-element list acting as a mutable triple-state
    flag: ``[False]`` = top-level, ``[True]`` = inside a triple-quoted
    string. We don't bother matching every quote edge case — the goal is
    just to avoid flagging lines where the literal is buried inside a
    docstring. Bare ``# ...`` comment lines are still subject to the
    spec rule ("注释里的成本数字同样是 drift 源"), so they ARE flagged.
    """
    # Naive: don't actually track triple-quote state for v1 — the scanner
    # is structural, not lexical. We rely on the fact that 0.0004 / etc.
    # appearing inside a docstring without a fee-bps assignment name on
    # the same line will only match COST_LITERAL_RE, which is intended
    # to fire on docstring mentions too (drift source).
    return False


# --- Per-file scanning -------------------------------------------------


def scan_file(path: Path) -> List[Violation]:
    """Return every cost-literal violation in ``path``.

    Patterns are line-oriented. Empty lines and lines that mention the
    approved constants (SMA34900_*) or the canonical cost_model import
    are silently exempt — those lines are pointing at the approved
    source rather than re-inventing the cost.
    """
    violations: List[Violation] = []
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return violations

    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw
        if not line.strip():
            continue

        # Self-reference guard: lines that import from or reference the
        # canonical cost_model get a free pass — the assignment regex
        # would otherwise flag any `FEE_BPS_PER_SIDE = ...` literal that
        # appears in `factor_backtester.py` (the source itself), and
        # this is the same logic generalised to user code that mentions
        # the canonical source by name.
        if "SMA34900" in line or "cost_model" in line or "factor_backtester" in line:
            continue

        m_cost = COST_LITERAL_RE.search(line)
        if m_cost:
            violations.append(
                Violation(
                    path=path,
                    lineno=lineno,
                    line=line,
                    pattern="literal",
                    matched=m_cost.group(0),
                )
            )

        m_assign = ASSIGN_RE.search(line)
        if m_assign:
            # Word-boundary safety: make sure the match starts at a real
            # word boundary even when the regex engine allows compound
            # identifiers (defensive — the upstream pattern already
            # anchors with \b on the LHS alternation).
            lhs = m_assign.group(1)
            value = m_assign.group("value")
            matched = f"{lhs} = {value}"
            # Skip pure-modifier usages where the LHS lives inside a
            # larger identifier (paranoia — \b already prevents this).
            start = m_assign.start(1)
            if start > 0 and (line[start - 1].isalnum() or line[start - 1] == "_"):
                continue
            violations.append(
                Violation(
                    path=path,
                    lineno=lineno,
                    line=line,
                    pattern="assign",
                    matched=matched,
                )
            )

    return violations


def scan(root: Path = DEFAULT_ROOT) -> List[Violation]:
    """Scan every non-exempt file under ``root`` and return violations."""
    out: List[Violation] = []
    for path in iter_targets(root):
        out.extend(scan_file(path))
    return out


# --- CLI ---------------------------------------------------------------

DEFAULT_ROOT_ARG = str(DEFAULT_ROOT)


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="check_inline_costs.py",
        description=(
            "Inline-cost drift scanner. Reports hard-coded execution-cost "
            "literals in strategy code. Read-only; does not modify files."
        ),
    )
    p.add_argument(
        "--root",
        default=DEFAULT_ROOT_ARG,
        help=(
            "Directory to scan (default: quant-loop/strategies/). "
            "Accepts any directory; exemption rules apply uniformly."
        ),
    )
    g = p.add_mutually_exclusive_group()
    g.add_argument(
        "--report",
        action="store_true",
        help="Print violations; always exit 0 (default when no flag given).",
    )
    g.add_argument(
        "--enforce",
        action="store_true",
        help=(
            "Print violations; exit 1 if any violation found, else 0. "
            "Use in CI to gate merges."
        ),
    )
    return p


def _format_violations(violations: Iterable[Violation]) -> Tuple[int, int]:
    files: set = set()
    n = 0
    for v in violations:
        print(v.render())
        n += 1
        files.add(v.path)
    return n, len(files)


def main(argv: List[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    root = Path(args.root)
    if not root.exists():
        print(f"error: root {root} does not exist", file=sys.stderr)
        return 2

    violations = scan(root)
    n_violations, n_files = _format_violations(violations)
    print(f"{n_violations} violations in {n_files} files")

    if args.enforce:
        return 1 if n_violations > 0 else 0
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised via subprocess
    raise SystemExit(main())
