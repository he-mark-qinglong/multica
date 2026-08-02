#!/usr/bin/env python3
"""Coverage report for ``_shared/`` (J1).

Runs the full ``_shared/`` pytest suite under ``coverage``, prints the
per-module coverage table plus total coverage, writes the text table to
``docs/coverage/coverage.txt``, and generates an HTML report under
``docs/coverage/`` (entry point ``docs/coverage/index.html``).

Usage::

    python3 scripts/coverage_report.py            # full run
    python3 scripts/coverage_report.py --no-html  # text table only

Requires the ``coverage`` package; if it is missing the script installs
it with ``pip3 install coverage`` (honouring the workspace proxy
convention in AGENTS.md when the proxy env vars are already set).

Exit code: 0 when the suite passes, otherwise the pytest exit code — a
red suite still produces a coverage report, but the failure is not
hidden from CI.
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DOCS_COVERAGE = REPO / "docs" / "coverage"
TEXT_REPORT = DOCS_COVERAGE / "coverage.txt"


def _ensure_coverage() -> None:
    try:
        import coverage  # noqa: F401
        return
    except ImportError:
        pass
    print("coverage not installed; running `pip3 install coverage` ...", flush=True)
    subprocess.run(
        [sys.executable, "-m", "pip", "install", "coverage"],
        check=True,
        cwd=REPO,
    )


def _run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    print("+ " + " ".join(cmd), flush=True)
    return subprocess.run(cmd, cwd=REPO, **kw)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--no-html", action="store_true", help="skip the HTML report")
    args = parser.parse_args()

    _ensure_coverage()
    DOCS_COVERAGE.mkdir(parents=True, exist_ok=True)

    # 1. Run the test suite under coverage.
    test = _run(
        [
            sys.executable, "-m", "coverage", "run",
            "--source=_shared", "--omit=*/test_*.py,*/__pycache__/*",
            "-m", "pytest", "_shared/", "-q",
        ],
    )

    # 2. Per-module text table (stdout + docs/coverage/coverage.txt).
    report = _run(
        [sys.executable, "-m", "coverage", "report", "-m"],
        capture_output=True,
        text=True,
    )
    print(report.stdout)
    if report.returncode != 0:
        print(report.stderr, file=sys.stderr)
        return report.returncode
    TEXT_REPORT.write_text(report.stdout, encoding="utf-8")
    print(f"text table written to {TEXT_REPORT.relative_to(REPO)}")

    # 3. HTML report into docs/coverage/.
    if not args.no_html:
        html = _run([sys.executable, "-m", "coverage", "html", "-d", str(DOCS_COVERAGE)])
        if html.returncode != 0:
            return html.returncode
        print(f"html report: {DOCS_COVERAGE.relative_to(REPO)}/index.html")

    # 4. Total coverage line, surfaced prominently.
    total = _run(
        [sys.executable, "-m", "coverage", "report", "--format=total"],
        capture_output=True,
        text=True,
    )
    if total.returncode == 0:
        print(f"TOTAL COVERAGE: {total.stdout.strip()}%")

    # Clean up the .coverage data file only if we created it in the repo root.
    data = REPO / ".coverage"
    if data.exists():
        shutil.copy(data, DOCS_COVERAGE / ".coverage.data")

    if test.returncode != 0:
        print(
            f"WARNING: pytest exited {test.returncode}; coverage above is partial.",
            file=sys.stderr,
        )
    return test.returncode


if __name__ == "__main__":
    sys.exit(main())
