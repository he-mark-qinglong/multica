"""Market making modules — Jane Street core domain supplement.

Provides the three capabilities missing from multica's quant-loop that
Jane Street's *Probability & Markets* guide identifies as central to a
trading firm:

  - **Expected Value**  → ``fair_value`` + ``reservation_price``
  - **Making Markets**  → ``quoting_engine`` + ``inventory``
  - **Adverse Selection** → ``adverse_selection``

All modules use immutable (frozen) dataclasses and pure functions,
matching the codebase convention.

References:
  - Avellaneda & Stoikov (2008), "High-frequency trading in a limit order book"
  - Glosten & Harris (1988), "Estimating the Components of the Bid/Ask Spread"
  - Albers et al. (2025), "The Market Maker's Dilemma", arXiv:2502.18625v2
  - Jane Street, "Probability & Markets Guide"
"""
from _shared.market_making.fair_value import (
    FairValue,
    MarketSnapshot,
    compute_fair_value,
    microprice,
    rolling_vwap,
    vpvr_fair_value,
)
from _shared.market_making.reservation_price import (
    ReservationPriceParams,
    reservation_price,
    rolling_sigma,
)
from _shared.market_making.inventory import (
    InventoryState,
    empty_inventory,
    flatten_required,
    inventory_skew,
    update_inventory,
)
from _shared.market_making.adverse_selection import (
    AdverseSelectionParams,
    AdverseSelectionState,
    ASK_LIFTED,
    BID_HIT,
    belief_update,
    decay_penalty,
    empty_state,
    is_quoting_allowed,
    on_fill,
)
from _shared.market_making.quoting_engine import (
    Quote,
    QuotingParams,
    compute_spread,
    generate_quotes,
)
from _shared.market_making.maker_simulator import (
    FillRecord,
    MakerSimConfig,
    RoundTrip,
    roundtrip_to_trade,
    simulate_market_making,
)
from _shared.market_making.kelly_sizing import (
    KellyParams,
    KellyResult,
    adaptive_kelly_multiplier,
    compute_kelly,
)
from _shared.market_making.tail_risk import (
    TailRiskResult,
    compute_tail_risk,
    historical_cvar,
    historical_var,
    cornish_fisher_var,
    max_consecutive_losses,
)
from _shared.market_making.portfolio_risk import (
    CorrelationResult,
    ERCResult,
    compute_correlation,
    erc_weights,
    portfolio_cvar,
    portfolio_var,
)
from _shared.market_making.live_quoter import (
    LiveQuoter,
    LiveQuoterConfig,
    QuoterTransport,
    run_live_quoter,
)
from _shared.market_making.optimal_spread import (
    OptimalSpreadParams,
    estimate_kappa,
    optimal_half_spread,
)
from _shared.market_making.multi_level import (
    MultiLevelParams,
    TierConfig,
    TierQuote,
    generate_multi_level_quotes,
)
from _shared.market_making.queue_position import (
    QueueParams,
    fill_probability,
    expected_fill_value,
    optimal_quote_aggressiveness,
)
from _shared.market_making.hmm_regime import (
    Regime,
    RegimeQuoteAdjustment,
    RegimeState,
    REGIME_ADJUSTMENTS,
    detect_regime,
    get_regime_adjustment,
)
from _shared.market_making.online_adverse import (
    OnlineASParams,
    OnlineASState,
    adaptive_belief_update,
    get_effective_cost,
    init_online_as,
    observe_fill,
)
from _shared.market_making.dynamic_erc import (
    DynamicERC,
    DynamicERCParams,
    DynamicERCResult,
)
from _shared.market_making.stress_test import (
    SCENARIOS,
    StressResult,
    StressScenario,
    run_all_stress_tests,
    run_stress_test,
)

__all__ = [
    # fair_value
    "FairValue", "MarketSnapshot", "compute_fair_value",
    "microprice", "rolling_vwap", "vpvr_fair_value",
    # reservation_price
    "ReservationPriceParams", "reservation_price", "rolling_sigma",
    # inventory
    "InventoryState", "empty_inventory", "flatten_required",
    "inventory_skew", "update_inventory",
    # adverse_selection
    "AdverseSelectionParams", "AdverseSelectionState",
    "ASK_LIFTED", "BID_HIT",
    "belief_update", "decay_penalty", "empty_state",
    "is_quoting_allowed", "on_fill",
    # quoting_engine
    "Quote", "QuotingParams", "compute_spread", "generate_quotes",
    # maker_simulator
    "FillRecord", "MakerSimConfig", "RoundTrip",
    "roundtrip_to_trade", "simulate_market_making",
    # kelly_sizing
    "KellyParams", "KellyResult",
    "adaptive_kelly_multiplier", "compute_kelly",
    # tail_risk
    "TailRiskResult", "compute_tail_risk",
    "historical_cvar", "historical_var",
    "cornish_fisher_var", "max_consecutive_losses",
    # portfolio_risk
    "CorrelationResult", "ERCResult",
    "compute_correlation", "erc_weights",
    "portfolio_cvar", "portfolio_var",
    # live_quoter
    "LiveQuoter", "LiveQuoterConfig",
    "QuoterTransport", "run_live_quoter",
    # optimal_spread
    "OptimalSpreadParams", "estimate_kappa", "optimal_half_spread",
    # multi_level
    "MultiLevelParams", "TierConfig", "TierQuote", "generate_multi_level_quotes",
    # queue_position
    "QueueParams", "fill_probability", "expected_fill_value",
    "optimal_quote_aggressiveness",
    # hmm_regime
    "Regime", "RegimeQuoteAdjustment", "RegimeState",
    "REGIME_ADJUSTMENTS", "detect_regime", "get_regime_adjustment",
    # online_adverse
    "OnlineASParams", "OnlineASState",
    "adaptive_belief_update", "get_effective_cost",
    "init_online_as", "observe_fill",
    # dynamic_erc
    "DynamicERC", "DynamicERCParams", "DynamicERCResult",
    # stress_test
    "SCENARIOS", "StressResult", "StressScenario",
    "run_all_stress_tests", "run_stress_test",
]
