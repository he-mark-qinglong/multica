"""ML model lifecycle management — train, version, drift-detect, retrain, expire.

Provides a framework-agnostic model registry that manages the full lifecycle
of ML models used in trading strategies:

1. **Training** — fit model on train data, validate on OOS, save with metadata
2. **Versioning** — track model versions with creation date, metrics, features
3. **Drift detection** — PSI (Population Stability Index) + KS test on features
4. **Retraining triggers** — auto-flag for retraining when drift exceeds threshold
5. **Expiry/cleanup** — models older than max_age are quarantined

Usage:
    registry = ModelRegistry(storage_dir="models/")
    entry = registry.train(
        name="btc_return_predictor",
        model_factory=lambda: RandomForestRegressor(n_estimators=100),
        X_train=X_train, y_train=y_train,
        X_val=X_val, y_val=y_val,
        feature_names=["momentum", "vol", "rsi"],
    )
    # Later: check if drift warrants retraining
    status = registry.check_drift("btc_return_predictor", X_live)
    if status.needs_retraining:
        registry.retrain("btc_return_predictor", X_new, y_new, X_val, y_val)
    # Cleanup old models
    registry.expire_stale(max_age_days=30)
"""
from __future__ import annotations

import hashlib
import pickle
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable

import numpy as np
import pandas as pd
from scipy import stats as sp_stats


class ModelStatus(str, Enum):
    ACTIVE = "active"
    QUARANTINED = "quarantined"
    EXPIRED = "expired"
    FAILED = "failed"


@dataclass
class DriftReport:
    """Result of drift detection on live data vs training distribution."""
    feature_psi: dict       # feature_name → PSI value
    feature_ks_pvalue: dict # feature_name → KS test p-value
    max_psi: float          # highest PSI across features
    needs_retraining: bool  # True if any feature exceeds drift threshold
    drift_summary: str      # human-readable summary


@dataclass
class ModelEntry:
    """Metadata for a trained model version."""
    name: str
    version: int
    status: ModelStatus
    created_at: str         # ISO timestamp
    model_path: str | None  # path to pickled model
    feature_names: list
    train_metrics: dict     # e.g., {"r2": 0.1, "rmse": 0.01}
    val_metrics: dict
    train_size: int
    val_size: int
    model_hash: str         # SHA256 of pickled model for integrity
    notes: str = ""


class ModelRegistry:
    """Manages the lifecycle of ML models.

    Models are stored in a directory structure:
        storage_dir/
            {name}/
                v{version}.pkl       # pickled model
                metadata.json        # ModelEntry as JSON
                reference_stats.json  # training distribution stats for drift
    """

    def __init__(self, storage_dir: str | Path = "models"):
        self.storage = Path(storage_dir)
        self.storage.mkdir(parents=True, exist_ok=True)

    def train(
        self,
        name: str,
        model_factory: Callable[[], Any],
        X_train: pd.DataFrame | np.ndarray,
        y_train: pd.Series | np.ndarray,
        X_val: pd.DataFrame | np.ndarray | None = None,
        y_val: pd.Series | np.ndarray | None = None,
        feature_names: list | None = None,
        notes: str = "",
    ) -> ModelEntry:
        """Train and register a new model version.

        Args:
            name: model name (e.g., "btc_return_predictor").
            model_factory: callable that returns a fresh, unfitted model.
            X_train, y_train: training data.
            X_val, y_val: validation data (optional but recommended).
            feature_names: list of feature names (inferred from X_train if DataFrame).
            notes: free-text notes for this version.

        Returns:
            ModelEntry with metadata.
        """
        model_dir = self.storage / name
        model_dir.mkdir(parents=True, exist_ok=True)

        # Determine version
        existing = sorted(model_dir.glob("v*.pkl"))
        version = len(existing) + 1

        # Infer feature names
        if feature_names is None:
            if isinstance(X_train, pd.DataFrame):
                feature_names = list(X_train.columns)
            else:
                feature_names = [f"f{i}" for i in range(X_train.shape[1])]

        # Train
        model = model_factory()
        model.fit(X_train, y_train)

        # Compute metrics
        train_metrics = _compute_regression_metrics(model, X_train, y_train)
        val_metrics = (
            _compute_regression_metrics(model, X_val, y_val)
            if X_val is not None and y_val is not None else {}
        )

        # Save model
        model_path = model_dir / f"v{version}.pkl"
        with open(model_path, "wb") as f:
            pickle.dump(model, f)

        # Hash for integrity
        model_hash = hashlib.sha256(model_path.read_bytes()).hexdigest()[:16]

        # Save reference stats for drift detection
        ref_stats = _compute_reference_stats(X_train, feature_names)
        import json
        stats_path = model_dir / "reference_stats.json"
        stats_path.write_text(json.dumps(ref_stats, indent=2, default=str))

        # Create entry
        entry = ModelEntry(
            name=name,
            version=version,
            status=ModelStatus.ACTIVE,
            created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            model_path=str(model_path),
            feature_names=feature_names,
            train_metrics=train_metrics,
            val_metrics=val_metrics,
            train_size=len(y_train),
            val_size=len(y_val) if y_val is not None else 0,
            model_hash=model_hash,
            notes=notes,
        )

        # Save metadata
        meta_path = model_dir / f"v{version}_metadata.json"
        meta_path.write_text(json.dumps(_entry_to_dict(entry), indent=2))

        # Mark previous versions as superseded
        for old in existing:
            old_meta = model_dir / f"v{len(existing)}.json"
            # Just leave old versions; they're versioned

        return entry

    def load(self, name: str, version: int | None = None) -> tuple[Any, ModelEntry]:
        """Load a model and its metadata.

        Args:
            name: model name.
            version: specific version (default: latest active).

        Returns:
            (model, ModelEntry) tuple.
        """
        model_dir = self.storage / name
        if not model_dir.exists():
            raise FileNotFoundError(f"Model '{name}' not found")

        if version is None:
            versions = sorted(model_dir.glob("v*.pkl"), key=lambda p: int(p.stem[1:]))
            if not versions:
                raise FileNotFoundError(f"No model versions for '{name}'")
            model_path = versions[-1]
            version = int(model_path.stem[1:])
        else:
            model_path = model_dir / f"v{version}.pkl"
            if not model_path.exists():
                raise FileNotFoundError(f"Version {version} not found")

        with open(model_path, "rb") as f:
            model = pickle.load(f)

        meta_path = model_dir / f"v{version}_metadata.json"
        import json
        meta = json.loads(meta_path.read_text())
        entry = _dict_to_entry(meta)

        return model, entry

    def check_drift(
        self,
        name: str,
        X_live: pd.DataFrame | np.ndarray,
        psi_threshold: float = 0.25,
        ks_alpha: float = 0.05,
    ) -> DriftReport:
        """Detect distribution drift in live data vs training distribution.

        Uses PSI (Population Stability Index) and KS test:
        - PSI < 0.1: no significant change
        - PSI 0.1-0.25: moderate drift, monitor
        - PSI > 0.25: significant drift, retraining recommended

        Args:
            name: model name.
            X_live: live feature data.
            psi_threshold: PSI value above which retraining is triggered.
            ks_alpha: p-value threshold for KS test.

        Returns:
            DriftReport with per-feature drift metrics.
        """
        model_dir = self.storage / name
        stats_path = model_dir / "reference_stats.json"
        if not stats_path.exists():
            raise FileNotFoundError(f"No reference stats for '{name}'")

        import json
        ref_stats = json.loads(stats_path.read_text())

        feature_psi = {}
        feature_ks_pvalue = {}

        if isinstance(X_live, pd.DataFrame):
            features = list(X_live.columns)
        else:
            features = ref_stats.get("feature_names", [])
            X_live = pd.DataFrame(X_live, columns=features)

        for feat in features:
            live_vals = X_live[feat].dropna().values if feat in X_live.columns else np.array([])
            ref = ref_stats.get(feat, {})

            if len(live_vals) < 10 or not ref:
                feature_psi[feat] = 0.0
                feature_ks_pvalue[feat] = 1.0
                continue

            # PSI calculation
            psi = _compute_psi(
                live_vals,
                ref.get("bins", []),
                ref.get("ref_freqs", []),
            )
            feature_psi[feat] = psi

            # KS test
            ref_samples = np.array(ref.get("samples", []))
            if len(ref_samples) > 0:
                _, ks_pval = sp_stats.ks_2samp(live_vals, ref_samples)
                feature_ks_pvalue[feat] = float(ks_pval)
            else:
                feature_ks_pvalue[feat] = 1.0

        max_psi = max(feature_psi.values()) if feature_psi else 0.0
        needs_retrain = max_psi > psi_threshold

        drifted = [f for f, p in feature_psi.items() if p > psi_threshold]
        summary = (
            f"Max PSI={max_psi:.4f} (threshold={psi_threshold}). "
            f"Drifted features: {drifted if drifted else 'none'}. "
            f"{'Retraining recommended.' if needs_retrain else 'No significant drift.'}"
        )

        return DriftReport(
            feature_psi=feature_psi,
            feature_ks_pvalue=feature_ks_pvalue,
            max_psi=max_psi,
            needs_retraining=needs_retrain,
            drift_summary=summary,
        )

    def expire_stale(self, max_age_days: int = 30) -> list:
        """Quarantine models older than max_age_days.

        Returns:
            List of quarantined model names+versions.
        """
        now = datetime.now(timezone.utc)
        quarantined = []

        for model_dir in self.storage.iterdir():
            if not model_dir.is_dir():
                continue
            for meta_path in model_dir.glob("v*_metadata.json"):
                import json
                meta = json.loads(meta_path.read_text())
                created = datetime.fromisoformat(meta["created_at"])
                age = (now - created).days
                if age > max_age_days and meta["status"] == ModelStatus.ACTIVE.value:
                    meta["status"] = ModelStatus.QUARANTINED.value
                    meta_path.write_text(json.dumps(meta, indent=2))
                    quarantined.append(f"{meta['name']} v{meta['version']}")

        return quarantined

    def list_models(self) -> pd.DataFrame:
        """List all registered models with metadata."""
        import json
        entries = []
        for model_dir in sorted(self.storage.iterdir()):
            if not model_dir.is_dir():
                continue
            for meta_path in model_dir.glob("v*_metadata.json"):
                meta = json.loads(meta_path.read_text())
                entries.append(meta)
        if not entries:
            return pd.DataFrame()
        return pd.DataFrame(entries)


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def _compute_regression_metrics(model, X, y) -> dict:
    """Compute standard regression metrics."""
    try:
        y_pred = model.predict(X)
        y_arr = np.asarray(y, dtype=float)
        p_arr = np.asarray(y_pred, dtype=float).ravel()
        if len(y_arr) != len(p_arr):
            return {"note": "shape mismatch"}
        rmse = float(np.sqrt(np.mean((y_arr - p_arr) ** 2)))
        ss_res = np.sum((y_arr - p_arr) ** 2)
        ss_tot = np.sum((y_arr - np.mean(y_arr)) ** 2)
        r2 = float(1 - ss_res / ss_tot) if ss_tot > 0 else 0.0
        return {"rmse": round(rmse, 6), "r2": round(r2, 4)}
    except Exception:
        return {}


def _compute_reference_stats(X, feature_names) -> dict:
    """Compute reference distribution stats for drift detection."""
    if isinstance(X, np.ndarray):
        X = pd.DataFrame(X, columns=feature_names)

    stats = {"feature_names": feature_names}
    for col in feature_names:
        if col not in X.columns:
            continue
        vals = X[col].dropna().values
        if len(vals) < 10:
            continue
        # Bin the reference distribution (10 quantile bins)
        bins = np.quantile(vals, np.linspace(0, 1, 11))
        bins = np.unique(bins)
        if len(bins) < 2:
            continue
        counts, _ = np.histogram(vals, bins=bins)
        ref_freqs = counts / len(vals)
        # Store subsample for KS test (max 1000 points)
        sample_size = min(len(vals), 1000)
        stats[col] = {
            "bins": bins.tolist(),
            "ref_freqs": ref_freqs.tolist(),
            "samples": np.random.choice(vals, sample_size, replace=False).tolist(),
        }
    return stats


def _compute_psi(live_vals: np.ndarray, bins: list, ref_freqs: list) -> float:
    """Compute Population Stability Index."""
    if len(bins) < 2 or len(live_vals) < 10:
        return 0.0
    bins_arr = np.array(bins)
    live_counts, _ = np.histogram(live_vals, bins=bins_arr)
    live_freqs = live_counts / len(live_vals)

    # Avoid division by zero
    eps = 1e-6
    ref = np.array(ref_freqs) + eps
    live = live_freqs + eps

    psi = np.sum((live - ref) * np.log(live / ref))
    return float(psi)


def _entry_to_dict(entry: ModelEntry) -> dict:
    return {
        "name": entry.name,
        "version": entry.version,
        "status": entry.status.value,
        "created_at": entry.created_at,
        "model_path": entry.model_path,
        "feature_names": entry.feature_names,
        "train_metrics": entry.train_metrics,
        "val_metrics": entry.val_metrics,
        "train_size": entry.train_size,
        "val_size": entry.val_size,
        "model_hash": entry.model_hash,
        "notes": entry.notes,
    }


def _dict_to_entry(d: dict) -> ModelEntry:
    return ModelEntry(
        name=d["name"],
        version=d["version"],
        status=ModelStatus(d["status"]),
        created_at=d["created_at"],
        model_path=d["model_path"],
        feature_names=d["feature_names"],
        train_metrics=d["train_metrics"],
        val_metrics=d["val_metrics"],
        train_size=d["train_size"],
        val_size=d["val_size"],
        model_hash=d["model_hash"],
        notes=d.get("notes", ""),
    )
