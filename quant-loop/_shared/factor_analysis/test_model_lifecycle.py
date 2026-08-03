"""Tests for ML model lifecycle management."""
import numpy as np
import pandas as pd
import pytest

from _shared.factor_analysis.model_lifecycle import (
    ModelRegistry, ModelStatus, DriftReport, ModelEntry,
)


class _DummyModel:
    """Minimal model with fit/predict for testing (no sklearn needed)."""
    def __init__(self):
        self.coef_ = None

    def fit(self, X, y):
        X_arr = np.asarray(X if not isinstance(X, pd.DataFrame) else X.values, dtype=float)
        y_arr = np.asarray(y, dtype=float)
        if X_arr.ndim == 1:
            X_arr = X_arr.reshape(-1, 1)
        # Simple OLS
        self.coef_, *_ = np.linalg.lstsq(X_arr, y_arr, rcond=None)
        return self

    def predict(self, X):
        X_arr = np.asarray(X if not isinstance(X, pd.DataFrame) else X.values, dtype=float)
        if X_arr.ndim == 1:
            X_arr = X_arr.reshape(-1, 1)
        return X_arr @ self.coef_


class TestModelTraining:
    def test_train_creates_versioned_model(self, tmp_path):
        reg = ModelRegistry(storage_dir=tmp_path / "models")
        rng = np.random.default_rng(42)
        X = pd.DataFrame({"f1": rng.normal(0, 1, 200), "f2": rng.normal(0, 1, 200)})
        y = pd.Series(X["f1"] * 0.5 + rng.normal(0, 0.1, 200))

        entry = reg.train(
            name="test_model",
            model_factory=lambda: _DummyModel(),
            X_train=X, y_train=y,
            X_val=X.iloc[:50], y_val=y.iloc[:50],
        )
        assert entry.version == 1
        assert entry.status == ModelStatus.ACTIVE
        assert entry.train_size == 200
        assert "r2" in entry.train_metrics

    def test_subsequent_train_increments_version(self, tmp_path):
        reg = ModelRegistry(storage_dir=tmp_path / "models")
        rng = np.random.default_rng(42)
        X = pd.DataFrame({"f1": rng.normal(0, 1, 100)})
        y = pd.Series(X["f1"] * 0.5)

        v1 = reg.train("m", lambda: _DummyModel(), X, y)
        v2 = reg.train("m", lambda: _DummyModel(), X, y)
        assert v1.version == 1
        assert v2.version == 2

    def test_metadata_saved(self, tmp_path):
        reg = ModelRegistry(storage_dir=tmp_path / "models")
        rng = np.random.default_rng(42)
        X = pd.DataFrame({"f1": rng.normal(0, 1, 100)})
        y = pd.Series(X["f1"] * 0.5)
        reg.train("m", lambda: _DummyModel(), X, y)

        assert (tmp_path / "models" / "m" / "v1.pkl").exists()
        assert (tmp_path / "models" / "m" / "v1_metadata.json").exists()
        assert (tmp_path / "models" / "m" / "reference_stats.json").exists()


class TestModelLoading:
    def test_load_latest(self, tmp_path):
        reg = ModelRegistry(storage_dir=tmp_path / "models")
        rng = np.random.default_rng(42)
        X = pd.DataFrame({"f1": rng.normal(0, 1, 100)})
        y = pd.Series(X["f1"] * 0.5)
        reg.train("m", lambda: _DummyModel(), X, y)
        reg.train("m", lambda: _DummyModel(), X, y)

        model, entry = reg.load("m")
        assert entry.version == 2
        assert hasattr(model, "predict")

    def test_load_specific_version(self, tmp_path):
        reg = ModelRegistry(storage_dir=tmp_path / "models")
        rng = np.random.default_rng(42)
        X = pd.DataFrame({"f1": rng.normal(0, 1, 100)})
        y = pd.Series(X["f1"] * 0.5)
        reg.train("m", lambda: _DummyModel(), X, y)

        model, entry = reg.load("m", version=1)
        assert entry.version == 1

    def test_load_nonexistent_raises(self, tmp_path):
        reg = ModelRegistry(storage_dir=tmp_path / "models")
        with pytest.raises(FileNotFoundError):
            reg.load("nonexistent")


class TestDriftDetection:
    def test_no_drift_for_same_distribution(self, tmp_path):
        reg = ModelRegistry(storage_dir=tmp_path / "models")
        rng = np.random.default_rng(42)
        X_train = pd.DataFrame({"f1": rng.normal(0, 1, 500)})
        y = pd.Series(X_train["f1"] * 0.5)
        reg.train("m", lambda: _DummyModel(), X_train, y)

        X_live = pd.DataFrame({"f1": rng.normal(0, 1, 200)})
        report = reg.check_drift("m", X_live)
        assert report.max_psi < 0.25
        assert not report.needs_retraining

    def test_detects_drift_for_shifted_distribution(self, tmp_path):
        reg = ModelRegistry(storage_dir=tmp_path / "models")
        rng = np.random.default_rng(42)
        X_train = pd.DataFrame({"f1": rng.normal(0, 1, 500)})
        y = pd.Series(X_train["f1"] * 0.5)
        reg.train("m", lambda: _DummyModel(), X_train, y)

        # Shifted live data
        X_live = pd.DataFrame({"f1": rng.normal(3, 1, 200)})
        report = reg.check_drift("m", X_live)
        assert report.max_psi > 0.1
        assert "f1" in report.feature_psi

    def test_drift_report_is_descriptive(self, tmp_path):
        reg = ModelRegistry(storage_dir=tmp_path / "models")
        rng = np.random.default_rng(42)
        X = pd.DataFrame({"f1": rng.normal(0, 1, 500)})
        y = pd.Series(X["f1"] * 0.5)
        reg.train("m", lambda: _DummyModel(), X, y)

        report = reg.check_drift("m", X.iloc[:100])
        assert isinstance(report.drift_summary, str)
        assert "PSI" in report.drift_summary


class TestExpiry:
    def test_expire_stale_quarantines_old(self, tmp_path):
        reg = ModelRegistry(storage_dir=tmp_path / "models")
        rng = np.random.default_rng(42)
        X = pd.DataFrame({"f1": rng.normal(0, 1, 100)})
        y = pd.Series(X["f1"] * 0.5)
        reg.train("m", lambda: _DummyModel(), X, y)

        # Manually backdate the metadata
        import json
        from datetime import datetime, timezone, timedelta
        meta_path = tmp_path / "models" / "m" / "v1_metadata.json"
        meta = json.loads(meta_path.read_text())
        old_date = (datetime.now(timezone.utc) - timedelta(days=60)).isoformat(timespec="seconds")
        meta["created_at"] = old_date
        meta_path.write_text(json.dumps(meta))

        quarantined = reg.expire_stale(max_age_days=30)
        assert len(quarantined) == 1
        assert "m v1" in quarantined[0]

    def test_expire_keeps_fresh(self, tmp_path):
        reg = ModelRegistry(storage_dir=tmp_path / "models")
        rng = np.random.default_rng(42)
        X = pd.DataFrame({"f1": rng.normal(0, 1, 100)})
        y = pd.Series(X["f1"] * 0.5)
        reg.train("m", lambda: _DummyModel(), X, y)

        quarantined = reg.expire_stale(max_age_days=30)
        assert len(quarantined) == 0


class TestListModels:
    def test_returns_dataframe(self, tmp_path):
        reg = ModelRegistry(storage_dir=tmp_path / "models")
        rng = np.random.default_rng(42)
        X = pd.DataFrame({"f1": rng.normal(0, 1, 100)})
        y = pd.Series(X["f1"] * 0.5)
        reg.train("model_a", lambda: _DummyModel(), X, y)
        reg.train("model_b", lambda: _DummyModel(), X, y)

        df = reg.list_models()
        assert isinstance(df, pd.DataFrame)
        assert len(df) == 2
        assert "name" in df.columns
        assert "version" in df.columns
