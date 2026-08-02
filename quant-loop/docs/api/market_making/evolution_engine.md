# `_shared.market_making.evolution_engine`

Source: `_shared/market_making/evolution_engine.py`

Genetic strategy evolution engine.

## class `EvolutionConfig(population_per_gen: 'int' = 50, max_generations: 'int' = 5, mutation_rate: 'float' = 0.3, mutation_sigma: 'float' = 0.2, elite_fraction: 'float' = 0.2, crossover_fraction: 'float' = 0.5, min_survivors_to_continue: 'int' = 1, convergence_patience: 'int' = 2) -> None`

Genetic algorithm parameters.

## `crossover(parent_a: 'StrategyCandidate', parent_b: 'StrategyCandidate', generation: 'int') -> 'StrategyCandidate'`

Uniform crossover: randomly pick each parameter from either parent.

| Parameter | Type | Default |
|---|---|---|
| `parent_a` | 'StrategyCandidate' | — |
| `parent_b` | 'StrategyCandidate' | — |
| `generation` | 'int' | — |

## `evolve(gen0_results: 'list[CandidateResult]', data_loader, config: 'EvolutionConfig' = EvolutionConfig(population_per_gen=50, max_generations=5, mutation_rate=0.3, mutation_sigma=0.2, elite_fraction=0.2, crossover_fraction=0.5, min_survivors_to_continue=1, convergence_patience=2), verbose: 'bool' = True) -> 'list[CandidateResult]'`

Run genetic evolution starting from generation-0 survivors.

| Parameter | Type | Default |
|---|---|---|
| `gen0_results` | 'list[CandidateResult]' | — |
| `data_loader` | — | — |
| `config` | 'EvolutionConfig' | EvolutionConfig(population_per_gen=50, max_generations=5, mutation_rate=0.3, mutation_sigma=0.2, elite_fraction=0.2, crossover_fraction=0.5, min_survivors_to_continue=1, convergence_patience=2) |
| `verbose` | 'bool' | True |

## `mutate(candidate: 'StrategyCandidate', config: 'EvolutionConfig', generation: 'int') -> 'StrategyCandidate'`

Apply random mutation to a candidate's parameters.

| Parameter | Type | Default |
|---|---|---|
| `candidate` | 'StrategyCandidate' | — |
| `config` | 'EvolutionConfig' | — |
| `generation` | 'int' | — |
