"""The derivations, what the store computes when structure exists, and the engine that computes them on read."""

from harness.derive.engine import (
    Derivation,
    Engine,
    Inputs,
    Judge,
    Registry,
    Unavailable,
    Value,
    canonical,
    computation_hash,
    digest,
    judgment_hash,
    text_hash,
)


def default_registry() -> Registry:
    """The store's derivations: the portfolio, the channels and betas, the markets, the interval, the judge's checks,
    the point, and the impact."""
    from harness.derive import channels, checks, impact, markets, point, portfolio, propagate

    return Registry(portfolio.DERIVATIONS + channels.DERIVATIONS + markets.DERIVATIONS + propagate.DERIVATIONS
                    + checks.DERIVATIONS + point.DERIVATIONS + impact.DERIVATIONS)


__all__ = [
    "Derivation", "Engine", "Inputs", "Judge", "Registry", "Unavailable", "Value",
    "canonical", "computation_hash", "default_registry", "digest", "judgment_hash", "text_hash",
]
