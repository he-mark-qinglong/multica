"""Tests for coverage trend tracker."""
import json
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

from scripts.track_coverage import append_history, trend_summary, HISTORY_FILE


class TestAppendHistory:
    def test_appends_entry(self, tmp_path, monkeypatch):
        monkeypatch.setattr("scripts.track_coverage.HISTORY_FILE", tmp_path / "hist.jsonl")
        entry = append_history({"line_coverage_pct": 85.5, "covered_lines": 100})
        assert entry["line_coverage_pct"] == 85.5
        assert "timestamp" in entry

        # Verify file written
        lines = (tmp_path / "hist.jsonl").read_text().strip().split("\n")
        assert len(lines) == 1
        parsed = json.loads(lines[0])
        assert parsed["line_coverage_pct"] == 85.5

    def test_multiple_appends(self, tmp_path, monkeypatch):
        hist = tmp_path / "hist.jsonl"
        monkeypatch.setattr("scripts.track_coverage.HISTORY_FILE", hist)
        append_history({"line_coverage_pct": 80.0})
        append_history({"line_coverage_pct": 82.0})
        append_history({"line_coverage_pct": 85.0})

        lines = hist.read_text().strip().split("\n")
        assert len(lines) == 3


class TestTrendSummary:
    def test_no_history(self, tmp_path, monkeypatch):
        monkeypatch.setattr("scripts.track_coverage.HISTORY_FILE", tmp_path / "nonexistent.jsonl")
        result = trend_summary()
        assert result["trend"] == "no history"

    def test_improving_trend(self, tmp_path, monkeypatch):
        hist = tmp_path / "hist.jsonl"
        for pct in [70.0, 75.0, 80.0, 85.0]:
            with open(hist, "a") as f:
                f.write(json.dumps({"line_coverage_pct": pct}) + "\n")
        monkeypatch.setattr("scripts.track_coverage.HISTORY_FILE", hist)

        result = trend_summary()
        assert result["trend"] == "improving"
        assert result["delta_over_period"] == 15.0

    def test_declining_trend(self, tmp_path, monkeypatch):
        hist = tmp_path / "hist.jsonl"
        for pct in [85.0, 80.0, 75.0, 70.0]:
            with open(hist, "a") as f:
                f.write(json.dumps({"line_coverage_pct": pct}) + "\n")
        monkeypatch.setattr("scripts.track_coverage.HISTORY_FILE", hist)

        result = trend_summary()
        assert result["trend"] == "declining"

    def test_stable_trend(self, tmp_path, monkeypatch):
        hist = tmp_path / "hist.jsonl"
        for pct in [80.0, 80.2, 80.1, 80.0]:
            with open(hist, "a") as f:
                f.write(json.dumps({"line_coverage_pct": pct}) + "\n")
        monkeypatch.setattr("scripts.track_coverage.HISTORY_FILE", hist)

        result = trend_summary()
        assert result["trend"] == "stable"
