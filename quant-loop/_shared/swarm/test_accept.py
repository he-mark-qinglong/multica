"""Tests for the W5-T12 swarm mechanical acceptance executor."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

# This test is two levels below quant-loop/. Add quant-loop/ so imports such as
# ``from _shared.swarm.accept import ...`` work when pytest runs from repo root.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from _shared.swarm.accept import run_acceptance  # noqa: E402


def write_manifest(run_dir: Path, items: list[dict[str, Any]]) -> Path:
    """Write a minimal valid swarm manifest and return its path."""
    run_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "run_id": "test-run",
        "created_at": "2026-07-25T14:00:00+08:00",
        "parent_issue": "SMA-36514",
        "items": items,
    }
    path = run_dir / "manifest.json"
    path.write_text(json.dumps(manifest))
    return path


def item(slug: str, cmd: str, **acceptance: Any) -> dict[str, Any]:
    return {
        "slug": slug,
        "files": [f"{slug}.txt"],
        "acceptance": {"cmd": cmd, **acceptance},
    }


def test_two_pass_one_fail(tmp_path: Path) -> None:
    write_manifest(
        tmp_path,
        [
            item("first-pass", "exit 0"),
            item("second-pass", "true"),
            item("third-fail", "exit 3"),
        ],
    )

    assert run_acceptance(tmp_path) == 1

    acceptance = json.loads((tmp_path / "acceptance.json").read_text())
    statuses = {result["slug"]: result["status"] for result in acceptance["results"]}
    assert statuses == {
        "first-pass": "passed",
        "second-pass": "passed",
        "third-fail": "failed",
    }
    failed = [r for r in acceptance["results"] if r["status"] == "failed"]
    assert [r["slug"] for r in failed] == ["third-fail"]
    assert failed[0]["exit_code"] == 3
    assert acceptance["overall"] == "failed"


def test_all_pass(tmp_path: Path) -> None:
    write_manifest(
        tmp_path,
        [item("first", "exit 0"), item("second", "exit 0")],
    )

    assert run_acceptance(tmp_path) == 0

    acceptance = json.loads((tmp_path / "acceptance.json").read_text())
    assert acceptance["overall"] == "passed"
    assert [r["status"] for r in acceptance["results"]] == [
        "passed",
        "passed",
    ]


def test_timeout(tmp_path: Path) -> None:
    write_manifest(
        tmp_path,
        [item("slow", "sleep 5", timeout_sec=1)],
    )

    assert run_acceptance(tmp_path) == 1

    acceptance = json.loads((tmp_path / "acceptance.json").read_text())
    assert acceptance["overall"] == "failed"
    assert acceptance["results"][0]["status"] == "timeout"
    assert acceptance["results"][0]["exit_code"] == -1


def test_missing_manifest(tmp_path: Path) -> None:
    assert run_acceptance(tmp_path) == 2
    assert not (tmp_path / "acceptance.json").exists()


def test_cwd_isolation(tmp_path: Path) -> None:
    write_manifest(tmp_path, [item("where", "pwd > where.txt")])

    assert run_acceptance(tmp_path) == 0

    where = (tmp_path / "where.txt").read_text().strip()
    assert Path(where).resolve() == tmp_path.resolve()
