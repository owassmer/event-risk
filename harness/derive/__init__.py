"""The derivations, what the store computes when structure exists, and the engine that computes them on read."""

from harness.derive.engine import (
    Derivation,
    Engine,
    Inputs,
    Judge,
    Registry,
    Unavailable,
    Value,
    Waiting,
    canonical,
    computation_hash,
    digest,
    judgment_hash,
    text_hash,
)


def default_registry() -> Registry:
    """The store's derivations: the portfolio and where its weight is, the channels and betas, the markets, the judge's
    checks, each structure's probabilities, an event's outcomes, and the impact."""
    from harness.derive import channels, checks, distribution, episode, impact, markets, outcomes, owned, portfolio, structure, together, valuation, worth_follow

    return Registry(portfolio.DERIVATIONS + owned.DERIVATIONS + channels.DERIVATIONS + markets.DERIVATIONS
                    + checks.DERIVATIONS + structure.DERIVATIONS + outcomes.DERIVATIONS + episode.DERIVATIONS
                    + impact.DERIVATIONS + valuation.DERIVATIONS + worth_follow.DERIVATIONS + together.DERIVATIONS
                    + distribution.DERIVATIONS)


__all__ = [
    "Derivation", "Engine", "Inputs", "Judge", "Registry", "Unavailable", "Value", "Waiting",
    "canonical", "computation_hash", "default_registry", "digest", "judgment_hash", "text_hash",
]
