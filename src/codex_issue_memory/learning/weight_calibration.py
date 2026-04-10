"""Phase 3.4 — Auto weight calibration via gradient-free coordinate descent.

Uses feature_outcome_log data to compute per-family weight adjustments
with ±0.05 step clamping for stability.
"""
from __future__ import annotations

import math
from typing import Any


_MAX_STEP = 0.05


def _sigmoid(x: float) -> float:
    if x >= 0:
        return 1.0 / (1.0 + math.exp(-x))
    ex = math.exp(x)
    return ex / (1.0 + ex)


def _log_loss(predictions: list[float], labels: list[int]) -> float:
    """Mean binary cross-entropy."""
    n = len(predictions)
    if n == 0:
        return 0.0
    eps = 1e-15
    total = 0.0
    for p, y in zip(predictions, labels):
        p = max(eps, min(1 - eps, p))
        total += y * math.log(p) + (1 - y) * math.log(1 - p)
    return -total / n


def _predict(samples: list[dict[str, Any]], weights: dict[str, float], feature_names: list[str]) -> list[float]:
    """Compute sigmoid(weighted sum) for each sample."""
    preds = []
    for sample in samples:
        features = sample["features"]
        score = sum(weights.get(fn, 0.0) * float(features.get(fn, 0.0)) for fn in feature_names)
        preds.append(_sigmoid(score))
    return preds


def compute_optimal_weights(
    *,
    samples: list[dict[str, Any]],
    feature_names: list[str],
    base_weights: dict[str, float],
    max_iterations: int = 50,
) -> dict[str, Any]:
    """Coordinate descent weight calibration with ±0.05 step clamping.

    Args:
        samples: [{features: {name: value}, reward: float}, ...]
        feature_names: which features to calibrate
        base_weights: current DEFAULT_WEIGHTS from ranker
        max_iterations: number of full passes over features

    Returns:
        {
            "weight_overrides": {feature: new_weight, ...},
            "deltas": {feature: delta, ...},
            "loss_before": float,
            "loss_after": float,
            "iterations": int,
            "samples_used": int,
        }
    """
    if not samples or not feature_names:
        return {
            "weight_overrides": {},
            "deltas": {},
            "loss_before": 0.0,
            "loss_after": 0.0,
            "iterations": 0,
            "samples_used": 0,
        }

    # Binary labels: reward > 0 → 1, else 0
    labels = [1 if float(s.get("reward", 0)) > 0 else 0 for s in samples]
    calibratable = [fn for fn in feature_names if fn in base_weights]
    if not calibratable:
        return {
            "weight_overrides": dict(base_weights),
            "deltas": {},
            "loss_before": 0.0,
            "loss_after": 0.0,
            "iterations": 0,
            "samples_used": len(samples),
        }

    # Start from base weights
    weights = dict(base_weights)
    loss_before = _log_loss(_predict(samples, weights, feature_names), labels)

    for iteration in range(max_iterations):
        improved = False
        for fn in calibratable:
            current = weights[fn]
            best_loss = _log_loss(_predict(samples, weights, feature_names), labels)
            best_w = current

            for delta in (-_MAX_STEP, _MAX_STEP):
                candidate_w = current + delta
                weights[fn] = candidate_w
                candidate_loss = _log_loss(_predict(samples, weights, feature_names), labels)
                if candidate_loss < best_loss - 1e-8:
                    best_loss = candidate_loss
                    best_w = candidate_w
                    improved = True

            weights[fn] = best_w

        if not improved:
            break

    loss_after = _log_loss(_predict(samples, weights, feature_names), labels)
    deltas = {fn: round(weights[fn] - base_weights.get(fn, 0.0), 6) for fn in calibratable if abs(weights[fn] - base_weights.get(fn, 0.0)) > 1e-8}

    return {
        "weight_overrides": {fn: round(weights[fn], 6) for fn in calibratable},
        "deltas": deltas,
        "loss_before": round(loss_before, 6),
        "loss_after": round(loss_after, 6),
        "iterations": iteration + 1 if samples else 0,
        "samples_used": len(samples),
    }
