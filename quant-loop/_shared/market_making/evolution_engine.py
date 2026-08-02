"""Genetic strategy evolution engine.

Takes surviving strategy candidates from the sweep, applies genetic
operators (mutation + crossover) to produce the next generation, tests
them, and iterates. This discovers non-obvious parameter combinations
that grid search would miss.

Lifecycle:
  Generation 0: grid sweep → survivors
  Generation 1+: mutate survivors + crossover pairs → test → rank → repeat

The engine stops when no generation produces new survivors (convergence)
or after max_generations.
"""
from __future__ import annotations

import random
import time
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from _shared.market_making.strategy_sweeper import (
    CandidateResult, StrategyCandidate, run_sweep, summarize_results,
)


@dataclass
class EvolutionConfig:
    """Genetic algorithm parameters."""

    population_per_gen: int = 50      # candidates per generation
    max_generations: int = 5          # stop after this many
    mutation_rate: float = 0.3        # probability of parameter mutation
    mutation_sigma: float = 0.2       # std of mutation (fraction of range)
    elite_fraction: float = 0.2       # top fraction carried over unchanged
    crossover_fraction: float = 0.5   # fraction from crossover
    min_survivors_to_continue: int = 1  # stop if fewer survivors
    convergence_patience: int = 2     # stop after N gens with no improvement


# Parameter ranges for mutation (per signal type)
PARAM_RANGES = {
    'funding_carry': {
        'threshold_bp': (1, 20),
        'sl_bp': (0, 1000),
        'rt_fee_bp': (4, 10),
    },
    'momentum': {
        'lookback': (5, 300),
        'min_move_bp': (1, 30),
        'tp_bp': (2, 30),
        'sl_bp': (4, 50),
        'hold_bars': (10, 300),
        'step': (1, 50),
        'rt_fee_bp': (4, 10),
    },
    'mean_revert': {
        'vwap_window': (20, 500),
        'deviation_bp': (2, 30),
        'tp_bp': (1, 20),
        'sl_bp': (3, 40),
        'hold_bars': (10, 300),
        'step': (1, 50),
        'rt_fee_bp': (4, 10),
    },
}


def mutate(
    candidate: StrategyCandidate,
    config: EvolutionConfig,
    generation: int,
) -> StrategyCandidate:
    """Apply random mutation to a candidate's parameters."""
    signal_type = candidate.signal_type
    ranges = PARAM_RANGES.get(signal_type, {})
    new_params = dict(candidate.params)

    for key, value in new_params.items():
        if key not in ranges:
            continue
        if random.random() < config.mutation_rate:
            lo, hi = ranges[key]
            if isinstance(value, int):
                delta = int((hi - lo) * config.mutation_sigma * random.gauss(0, 1))
                new_params[key] = max(lo, min(hi, value + delta))
            else:
                delta = (hi - lo) * config.mutation_sigma * random.gauss(0, 1)
                new_params[key] = max(lo, min(hi, value + delta))

    return StrategyCandidate(
        id=f"E{generation}-{random.randint(10000, 99999)}",
        symbol=candidate.symbol,
        signal_type=signal_type,
        timeframe=candidate.timeframe,
        params=new_params,
        generation=generation,
    )


def crossover(
    parent_a: StrategyCandidate,
    parent_b: StrategyCandidate,
    generation: int,
) -> StrategyCandidate:
    """Uniform crossover: randomly pick each parameter from either parent."""
    if parent_a.signal_type != parent_b.signal_type:
        return parent_a  # can't cross different signal types

    new_params = {}
    all_keys = set(parent_a.params.keys()) | set(parent_b.params.keys())
    for key in all_keys:
        if key in parent_a.params and key in parent_b.params:
            new_params[key] = random.choice([parent_a.params[key], parent_b.params[key]])
        elif key in parent_a.params:
            new_params[key] = parent_a.params[key]
        else:
            new_params[key] = parent_b.params[key]

    return StrategyCandidate(
        id=f"X{generation}-{random.randint(10000, 99999)}",
        symbol=random.choice([parent_a.symbol, parent_b.symbol]),
        signal_type=parent_a.signal_type,
        timeframe=parent_a.timeframe,
        params=new_params,
        generation=generation,
    )


def evolve(
    gen0_results: list[CandidateResult],
    data_loader,
    config: EvolutionConfig = EvolutionConfig(),
    verbose: bool = True,
) -> list[CandidateResult]:
    """Run genetic evolution starting from generation-0 survivors.

    Parameters
    ----------
    gen0_results : list of CandidateResult
        Full results from generation-0 sweep (sorted by deflated_sharpe).
    data_loader : callable
        Same as in strategy_sweeper.run_sweep.
    config : EvolutionConfig
    verbose : bool

    Returns
    -------
    list of CandidateResult
        All results across all generations, sorted by deflated_sharpe.
    """
    all_results = list(gen0_results)
    best_dsr = gen0_results[0].deflated_sharpe if gen0_results else -999
    gens_without_improvement = 0

    # Extract survivors (profitable or near-profitable)
    survivors = [r for r in gen0_results if r.avg_pnl_bp > -2][:20]  # top 20 near-profitable

    if not survivors:
        if verbose:
            print("No survivors to evolve. Exiting.")
        return all_results

    for gen in range(1, config.max_generations + 1):
        if verbose:
            print(f"\n{'='*70}")
            print(f"GENERATION {gen} — {len(survivors)} survivors from gen {gen-1}")
            print(f"{'='*70}")

        # Build next generation
        next_gen: list[StrategyCandidate] = []

        # Elite: carry over top performers unchanged
        n_elite = max(1, int(len(survivors) * config.elite_fraction))
        for r in survivors[:n_elite]:
            next_gen.append(r.candidate)

        # Mutations
        n_mutations = int(config.population_per_gen * config.mutation_rate)
        for _ in range(n_mutations):
            parent = random.choice(survivors).candidate
            next_gen.append(mutate(parent, config, gen))

        # Crossovers
        n_crossovers = config.population_per_gen - len(next_gen)
        for _ in range(n_crossovers):
            if len(survivors) >= 2:
                a, b = random.sample(survivors, 2)
                next_gen.append(crossover(a.candidate, b.candidate, gen))
            elif survivors:
                next_gen.append(mutate(survivors[0].candidate, config, gen))

        # Test this generation
        gen_results = run_sweep(next_gen, data_loader, n_trials=len(all_results), verbose=verbose)

        # Track best
        gen_best_dsr = gen_results[0].deflated_sharpe if gen_results else -999
        if gen_best_dsr > best_dsr:
            best_dsr = gen_best_dsr
            gens_without_improvement = 0
            if verbose:
                print(f"  ★ NEW BEST DSR: {best_dsr:.3f}")
        else:
            gens_without_improvement += 1
            if verbose:
                print(f"  No improvement ({gens_without_improvement}/{config.convergence_patience})")

        all_results.extend(gen_results)

        # Select survivors for next generation
        new_survivors = [r for r in gen_results if r.avg_pnl_bp > -2][:20]
        if new_survivors:
            survivors = new_survivors

        if len([r for r in gen_results if r.passed_gates]) < config.min_survivors_to_continue:
            if verbose:
                print(f"  Only {len([r for r in gen_results if r.passed_gates])} passed — stopping.")
            break

        if gens_without_improvement >= config.convergence_patience:
            if verbose:
                print(f"  Converged — {config.convergence_patience} gens without improvement.")
            break

    # Final sort
    all_results.sort(key=lambda r: r.deflated_sharpe, reverse=True)
    return all_results
