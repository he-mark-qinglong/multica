"""Strategy research kit — registry, signal composition, hot config reload,
ML gateway, feature pipeline, and triple-barrier labels.

New-module package per work package A (metrics A7/A8/A15/A16/A17/A18).
Each module is self-contained; imports between them are lazy so no
optional dependency (e.g. a pickled sklearn model) is required at import
time.
"""
from _shared.strategy_kit.registry import (
    IndicatorSpec,
    ParamSpec,
    get_indicator,
    get_spec,
    list_indicators,
    register_indicator,
)
from _shared.strategy_kit.composer import ComposerConfig, compose_signals
from _shared.strategy_kit.hot_reload import ConfigReloader, ReloadEvent
from _shared.strategy_kit.ml_gateway import (
    MLGateway,
    ModelBundle,
    PassthroughModel,
    load_pickle_model,
    make_passthrough_bundle,
)
from _shared.strategy_kit.feature_pipeline import (
    FeatureDef,
    FeaturePipeline,
    LookaheadError,
)
from _shared.strategy_kit.labels import BarrierConfig, triple_barrier_labels

__all__ = [
    "BarrierConfig",
    "ComposerConfig",
    "ConfigReloader",
    "FeatureDef",
    "FeaturePipeline",
    "IndicatorSpec",
    "LookaheadError",
    "MLGateway",
    "ModelBundle",
    "ParamSpec",
    "PassthroughModel",
    "ReloadEvent",
    "compose_signals",
    "get_indicator",
    "get_spec",
    "list_indicators",
    "load_pickle_model",
    "make_passthrough_bundle",
    "register_indicator",
    "triple_barrier_labels",
]
