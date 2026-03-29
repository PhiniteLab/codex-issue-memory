from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import blake2b
from math import sqrt
from random import Random
from typing import Any


@dataclass(slots=True)
class BetaPosterior:
    alpha: float
    beta: float
    mean: float
    std: float
    sample: float
    effective_observations: float
    decay_factor: float
    prior_alpha: float
    prior_beta: float
    updated_at: str = ""


def _parse_iso_datetime(value: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def posterior_mean(alpha: float, beta: float) -> float:
    total = float(alpha) + float(beta)
    if total <= 0.0:
        return 0.5
    return float(alpha) / total


def posterior_std(alpha: float, beta: float) -> float:
    total = float(alpha) + float(beta)
    if total <= 1.0:
        return 0.25
    numerator = float(alpha) * float(beta)
    denominator = (total * total) * (total + 1.0)
    if denominator <= 0.0:
        return 0.25
    return sqrt(max(numerator / denominator, 0.0))


def effective_observations(alpha: float, beta: float, *, prior_alpha: float, prior_beta: float) -> float:
    return max(float(alpha) + float(beta) - float(prior_alpha) - float(prior_beta), 0.0)


def decay_beta_parameters(
    *,
    alpha: float,
    beta: float,
    updated_at: str,
    half_life_days: int,
    prior_alpha: float,
    prior_beta: float,
) -> tuple[float, float, float]:
    if half_life_days <= 0:
        return max(alpha, prior_alpha), max(beta, prior_beta), 1.0
    updated_dt = _parse_iso_datetime(updated_at)
    if updated_dt is None:
        return max(alpha, prior_alpha), max(beta, prior_beta), 1.0
    age_days = max((datetime.now(timezone.utc) - updated_dt).total_seconds() / 86400.0, 0.0)
    if age_days <= 0.0:
        return max(alpha, prior_alpha), max(beta, prior_beta), 1.0
    decay = 0.5 ** (age_days / float(half_life_days))
    decayed_alpha = prior_alpha + max(alpha - prior_alpha, 0.0) * decay
    decayed_beta = prior_beta + max(beta - prior_beta, 0.0) * decay
    return decayed_alpha, decayed_beta, decay


def deterministic_rng(*parts: Any) -> Random:
    payload = "||".join(str(part) for part in parts)
    digest = blake2b(payload.encode("utf-8"), digest_size=16).digest()
    seed = int.from_bytes(digest, "big")
    return Random(seed)


def build_beta_posterior(
    *,
    alpha: float,
    beta: float,
    updated_at: str,
    half_life_days: int,
    prior_alpha: float,
    prior_beta: float,
    seed_parts: tuple[Any, ...],
) -> BetaPosterior:
    decayed_alpha, decayed_beta, decay = decay_beta_parameters(
        alpha=float(alpha),
        beta=float(beta),
        updated_at=updated_at,
        half_life_days=half_life_days,
        prior_alpha=prior_alpha,
        prior_beta=prior_beta,
    )
    rng = deterministic_rng(*seed_parts)
    sample = rng.betavariate(max(decayed_alpha, 1e-6), max(decayed_beta, 1e-6))
    return BetaPosterior(
        alpha=decayed_alpha,
        beta=decayed_beta,
        mean=posterior_mean(decayed_alpha, decayed_beta),
        std=posterior_std(decayed_alpha, decayed_beta),
        sample=float(sample),
        effective_observations=effective_observations(
            decayed_alpha,
            decayed_beta,
            prior_alpha=prior_alpha,
            prior_beta=prior_beta,
        ),
        decay_factor=decay,
        prior_alpha=prior_alpha,
        prior_beta=prior_beta,
        updated_at=updated_at,
    )


def shrinkage_weight(effective_obs: float, *, lambda_value: float) -> float:
    if effective_obs <= 0.0:
        return 0.0
    return float(effective_obs) / (float(effective_obs) + max(float(lambda_value), 1e-6))


__all__ = [
    "BetaPosterior",
    "build_beta_posterior",
    "decay_beta_parameters",
    "deterministic_rng",
    "effective_observations",
    "posterior_mean",
    "posterior_std",
    "shrinkage_weight",
]
