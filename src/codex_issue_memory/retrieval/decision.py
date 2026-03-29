from __future__ import annotations

from ..models import MatchDecision
from .ranker import RankedCandidate


class MatchDecisionPolicy:
    """Convert ranked candidates into match / ambiguous / abstain decisions."""

    MIN_SPECIFICITY_FOR_WEAK_MATCH = 0.12
    MIN_SPECIFICITY_FOR_ACCEPT = 0.16
    MIN_SPECIFICITY_GAP_FOR_CONTEXT_DOMINANCE = 0.22

    def __init__(self, *, accept_threshold: float, weak_threshold: float, ambiguity_margin: float) -> None:
        self.accept_threshold = accept_threshold
        self.weak_threshold = weak_threshold
        self.ambiguity_margin = ambiguity_margin

    @classmethod
    def _specificity(cls, ranked: RankedCandidate) -> float:
        features = ranked.features
        root_score = float(features.get("root_score", 0.0))
        text_overlap = float(features.get("text_overlap_score", 0.0))
        exception_overlap = float(features.get("exception_overlap_score", 0.0))
        example_score = float(features.get("example_score", 0.0))
        episode_score = float(features.get("episode_score", 0.0))
        command_score = float(features.get("command_score", 0.0))
        path_score = float(features.get("path_score", 0.0))
        variant_score = float(features.get("variant_score", 0.0))
        return max(
            root_score,
            exception_overlap,
            text_overlap + 0.40 * max(example_score, episode_score),
            variant_score + 0.30 * max(command_score, path_score),
        )

    def decide(self, ranked: list[RankedCandidate]) -> MatchDecision:
        if not ranked:
            return MatchDecision(status="abstain", confidence=0.0, reason="no-supported-candidates")

        top_score = ranked[0].score
        second_score = ranked[1].score if len(ranked) > 1 else 0.0
        gap = max(top_score - second_score, 0.0)
        specificity = self._specificity(ranked[0])
        second_specificity = self._specificity(ranked[1]) if len(ranked) > 1 else 0.0
        specificity_gap = max(specificity - second_specificity, 0.0)

        if top_score >= self.weak_threshold and specificity < self.MIN_SPECIFICITY_FOR_WEAK_MATCH:
            return MatchDecision(
                status="abstain",
                confidence=top_score,
                reason="candidate-not-specific-enough",
                top_score=top_score,
                second_score=second_score,
                gap=gap,
            )

        if top_score >= self.accept_threshold and specificity >= self.MIN_SPECIFICITY_FOR_ACCEPT:
            if gap >= self.ambiguity_margin:
                return MatchDecision(
                    status="match",
                    confidence=top_score,
                    reason="top-candidate-clearly-ahead",
                    top_score=top_score,
                    second_score=second_score,
                    gap=gap,
                )
            if specificity_gap >= self.MIN_SPECIFICITY_GAP_FOR_CONTEXT_DOMINANCE:
                return MatchDecision(
                    status="match",
                    confidence=top_score,
                    reason="context-specific-top-candidate",
                    top_score=top_score,
                    second_score=second_score,
                    gap=gap,
                )

        if top_score >= self.weak_threshold:
            reason = "top-score-below-accept-threshold"
            if len(ranked) > 1 and gap < self.ambiguity_margin:
                reason = "top-candidates-too-close"
            return MatchDecision(
                status="ambiguous",
                confidence=top_score,
                reason=reason,
                top_score=top_score,
                second_score=second_score,
                gap=gap,
            )

        return MatchDecision(
            status="abstain",
            confidence=top_score,
            reason="top-score-below-weak-threshold",
            top_score=top_score,
            second_score=second_score,
            gap=gap,
        )
