from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..settings import Settings


@dataclass(slots=True)
class SafeOverrideResult:
    chosen_key: str
    promoted: bool
    baseline_key: str
    promoted_key: str | None
    reason: str
    margin: float
    evidence: float


class SafeOverridePolicy:
    """Conservative gate that prevents the learning overlay from replacing a strong baseline too early."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def choose(
        self,
        *,
        baseline_key: str,
        baseline_score: float,
        analyses: dict[str, Any],
    ) -> SafeOverrideResult:
        if baseline_key not in analyses:
            return SafeOverrideResult(
                chosen_key=baseline_key,
                promoted=False,
                baseline_key=baseline_key,
                promoted_key=None,
                reason="baseline-missing-bandit-analysis",
                margin=0.0,
                evidence=0.0,
            )

        sorted_keys = sorted(
            analyses,
            key=lambda key: (
                -float(analyses[key].final_score),
                -float(analyses[key].conservative_score),
                -float(analyses[key].effective_evidence),
                str(key),
            ),
        )
        best_key = sorted_keys[0]
        best = analyses[best_key]

        if best_key == baseline_key:
            return SafeOverrideResult(
                chosen_key=baseline_key,
                promoted=False,
                baseline_key=baseline_key,
                promoted_key=None,
                reason="baseline-remains-best",
                margin=0.0,
                evidence=float(best.effective_evidence),
            )

        evidence = float(best.effective_evidence)
        conservative_margin = float(best.conservative_score) - float(baseline_score)
        if evidence + 1e-6 < float(self.settings.minimum_strategy_evidence):
            return SafeOverrideResult(
                chosen_key=baseline_key,
                promoted=False,
                baseline_key=baseline_key,
                promoted_key=None,
                reason="insufficient-strategy-evidence",
                margin=conservative_margin,
                evidence=evidence,
            )
        if conservative_margin < float(self.settings.safe_override_margin):
            return SafeOverrideResult(
                chosen_key=baseline_key,
                promoted=False,
                baseline_key=baseline_key,
                promoted_key=None,
                reason="conservative-score-below-margin",
                margin=conservative_margin,
                evidence=evidence,
            )
        if float(best.negative_penalty) >= 0.20:
            return SafeOverrideResult(
                chosen_key=baseline_key,
                promoted=False,
                baseline_key=baseline_key,
                promoted_key=None,
                reason="negative-applicability-penalty",
                margin=conservative_margin,
                evidence=evidence,
            )
        return SafeOverrideResult(
            chosen_key=best_key,
            promoted=True,
            baseline_key=baseline_key,
            promoted_key=best_key,
            reason="safe-override-approved",
            margin=conservative_margin,
            evidence=evidence,
        )


__all__ = ["SafeOverridePolicy", "SafeOverrideResult"]
