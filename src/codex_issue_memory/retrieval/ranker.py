from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..learning import SafeOverridePolicy, StrategyThompsonBandit
from ..models import QueryProfile
from ..settings import Settings
from ..storage import IssueMemoryStore
from .features import build_candidate_features


@dataclass(slots=True)
class RankedCandidate:
    candidate: dict[str, Any]
    score: float
    features: dict[str, float]
    reasons: list[str]


class HeuristicRanker:
    """Deterministic weighted ranker with optional conservative strategy-bandit overlay."""

    DEFAULT_WEIGHTS: dict[str, float] = {
        "scope_score": 0.07,
        "family_score": 0.12,
        "root_score": 0.18,
        "lexical_score": 0.08,
        "dense_score": 0.10,
        "text_overlap_score": 0.10,
        "tag_overlap_score": 0.03,
        "exception_overlap_score": 0.06,
        "example_score": 0.05,
        "episode_score": 0.08,
        "command_score": 0.05,
        "path_score": 0.05,
        "env_score": 0.05,
        "entity_match_score": 0.09,
        "entity_conflict_penalty_score": -0.18,
        "variant_score": 0.12,
        "support_score": 0.05,
        "recency_score": 0.02,
        "feedback_score": 0.12,
        "success_prior_score": 0.06,
        "proven_score": 0.08,
        "session_boost_score": 0.04,
        "session_penalty_score": -0.24,
    }

    def __init__(self, store: IssueMemoryStore | None = None, settings: Settings | None = None) -> None:
        self.store = store
        self.settings = settings
        self._weight_overrides: dict[str, dict[str, float]] = {}
        if store is not None and settings is not None and settings.enable_calibration_profile:
            profile = store.load_calibration_profile()
            raw_overrides = profile.get("weight_overrides") if isinstance(profile, dict) else None
            if isinstance(raw_overrides, dict):
                for family, overrides in raw_overrides.items():
                    if isinstance(overrides, dict):
                        self._weight_overrides[str(family)] = {
                            str(k): float(v) for k, v in overrides.items()
                            if str(k) in self.DEFAULT_WEIGHTS
                        }
        strategy_bandit_active = store is not None and settings is not None and settings.enable_strategy_bandit
        self.strategy_bandit = (
            StrategyThompsonBandit(store, settings)
            if strategy_bandit_active and store is not None and settings is not None
            else None
        )
        self.safe_override = (
            SafeOverridePolicy(settings)
            if strategy_bandit_active and settings is not None
            else None
        )

    @staticmethod
    def _clamp(value: float, *, low: float = 0.0, high: float = 0.999) -> float:
        return min(max(float(value), low), high)

    def _weights_for_family(self, error_family: str) -> dict[str, float]:
        """Return merged weights: DEFAULT_WEIGHTS overridden by family-specific calibration."""
        overrides = self._weight_overrides.get(error_family)
        if not overrides:
            return self.DEFAULT_WEIGHTS
        merged = dict(self.DEFAULT_WEIGHTS)
        merged.update(overrides)
        return merged

    @staticmethod
    def _candidate_key(item: RankedCandidate) -> str:
        candidate = item.candidate
        variant = candidate.get("best_variant") or {}
        pattern_id = int(candidate.get("pattern_id", candidate.get("id", 0)) or 0)
        variant_id = int(candidate.get("variant_id") or variant.get("id") or 0)
        return f"{pattern_id}:{variant_id}"

    def _preference_adjustment(
        self,
        profile: QueryProfile,
        candidate: dict[str, Any],
        preference_rules: list[dict[str, Any]] | None,
    ) -> tuple[float, list[str]]:
        if not preference_rules or self.settings is None or not self.settings.enable_preference_rules:
            return 0.0, []
        variant = candidate.get("best_variant") or {}
        strategy_key = str(variant.get("strategy_key", "")).strip()
        candidate_family = str(candidate.get("error_family", "")).strip()
        if not strategy_key:
            return 0.0, []
        adjustment = 0.0
        reasons: list[str] = []
        for rule in preference_rules:
            rule_strategy = str(rule.get("strategy_key", "")).strip()
            if not rule_strategy or rule_strategy != strategy_key:
                continue
            rule_family = str(rule.get("error_family", "")).strip()
            if rule_family and rule_family not in {candidate_family, profile.error_family}:
                continue
            contribution = (
                float(rule.get("weight", 0.0))
                * float(rule.get("match_score", 0.0))
                * float(self.settings.preference_overlay_scale)
            )
            if abs(contribution) <= 1e-9:
                continue
            adjustment += contribution
            prefix = "preference-rule:" if contribution >= 0.0 else "avoidance-rule:"
            reason = prefix + rule_strategy
            if reason not in reasons:
                reasons.append(reason)
        cap = float(self.settings.max_preference_adjustment)
        if adjustment > cap:
            adjustment = cap
        elif adjustment < -cap:
            adjustment = -cap
        return adjustment, reasons

    def score(
        self,
        profile: QueryProfile,
        candidate: dict[str, Any],
        *,
        project_scope: str,
        preference_rules: list[dict[str, Any]] | None = None,
        error_family: str = "",
    ) -> RankedCandidate:
        # Phase 4.3: Fetch entity importance weights from store
        ei_weights: dict[str, float] | None = None
        if self.store is not None and error_family:
            variant = candidate.get("best_variant") or {}
            raw_entity_slots = variant.get("entity_slots_json") if isinstance(variant, dict) else {}
            entity_slots = raw_entity_slots if isinstance(raw_entity_slots, dict) else {}
            all_keys = list(set(list(profile.entity_slots.keys()) + list(entity_slots.keys())))
            if all_keys:
                ei_weights = self.store.query_entity_importance(error_family, all_keys)

        features, reasons = build_candidate_features(
            profile, candidate, project_scope=project_scope,
            entity_importance=ei_weights if ei_weights else None,
        )
        weights = self._weights_for_family(error_family) if error_family else self.DEFAULT_WEIGHTS
        base_total = 0.0
        for name, weight in weights.items():
            base_total += weight * features.get(name, 0.0)

        preference_adjustment, preference_reasons = self._preference_adjustment(profile, candidate, preference_rules)
        for reason in preference_reasons:
            if reason not in reasons:
                reasons.append(reason)

        total = self._clamp(base_total + preference_adjustment)
        features["base_score"] = total
        features["bandit_adjustment"] = 0.0
        features["preference_adjustment"] = preference_adjustment
        features["strategy_bandit_adjustment"] = 0.0
        features["strategy_bandit_final_score"] = total
        return RankedCandidate(candidate=candidate, score=total, features=features, reasons=reasons)

    @staticmethod
    def _quantize(value: float) -> int:
        """Quantize a float score to a fixed-precision integer to ensure deterministic ordering."""
        return int(round(value * 10000))

    @staticmethod
    def _sort_key(
        item: RankedCandidate,
    ) -> tuple[int, int, int, int, int, int, int, int, int, int, int]:
        candidate = item.candidate
        best_variant = candidate.get("best_variant") or {}
        variant_confidence = float(best_variant.get("confidence", 0.0)) if isinstance(best_variant, dict) else 0.0
        variant_id = int(candidate.get("variant_id", 0) or 0)
        pattern_id = int(candidate.get("pattern_id", candidate.get("id", 10**9)))
        _q = HeuristicRanker._quantize
        return (
            -_q(item.score),
            -_q(item.features.get("preference_adjustment", 0.0)),
            -_q(item.features.get("bandit_adjustment", 0.0)),
            -_q(item.features.get("dense_score", 0.0)),
            -_q(item.features.get("variant_score", 0.0)),
            -_q(item.features.get("feedback_score", 0.0)),
            -_q(item.features.get("root_score", 0.0)),
            -_q(item.features.get("family_score", 0.0)),
            -_q(variant_confidence),
            pattern_id,
            variant_id,
        )

    def _apply_strategy_bandit(
        self,
        profile: QueryProfile,
        ranked: list[RankedCandidate],
    ) -> list[RankedCandidate]:
        if not ranked or self.strategy_bandit is None or self.safe_override is None:
            return ranked

        evaluation_limit = min(len(ranked), 5)
        head = ranked[:evaluation_limit]
        tail = ranked[evaluation_limit:]

        analyses = self.strategy_bandit.score_candidates(profile, head, project_scope=profile.project_scope)
        if not analyses:
            return ranked

        baseline = head[0]
        baseline_key = self._candidate_key(baseline)
        selection = self.safe_override.choose(
            baseline_key=baseline_key,
            baseline_score=float(baseline.score),
            analyses=analyses,
        )

        baseline_score = float(baseline.score)
        promoted_key = selection.promoted_key if selection.promoted else None

        for item in head:
            key = self._candidate_key(item)
            analysis = analyses.get(key)
            if analysis is None:
                continue
            item.features["strategy_bandit_adjustment"] = float(analysis.adjustment)
            item.features["strategy_bandit_final_score"] = float(analysis.final_score)
            item.features["strategy_posterior_mean_score"] = float(analysis.strategy_mean)
            item.features["strategy_sample_score"] = float(analysis.strategy_sample)
            item.features["variant_posterior_mean_score"] = float(analysis.variant_mean)
            item.features["variant_sample_score"] = float(analysis.variant_sample)
            item.features["negative_applicability_penalty"] = float(analysis.negative_penalty)
            if analysis.adjustment > 1e-9:
                item.features["bandit_adjustment"] = float(analysis.adjustment)
            for reason in analysis.reasons:
                if reason not in item.reasons:
                    item.reasons.append(reason)

        shadow_mode = bool(self.settings.enable_strategy_bandit_shadow_mode) if self.settings is not None else False
        if promoted_key is not None and shadow_mode:
            for item in head:
                key = self._candidate_key(item)
                analysis = analyses.get(key)
                if analysis is None:
                    continue
                item.features["strategy_bandit_shadow_mode"] = 1.0
                item.features["strategy_bandit_shadow_promoted"] = 1.0 if key == promoted_key else 0.0
                if key == promoted_key and "strategy-bandit-shadow-promote" not in item.reasons:
                    item.reasons.insert(0, "strategy-bandit-shadow-promote")
                elif key == baseline_key and "strategy-bandit-shadow-hold" not in item.reasons:
                    item.reasons.append("strategy-bandit-shadow-hold")
            head = [baseline] + sorted(head[1:], key=self._sort_key)
        elif promoted_key is not None:
            for item in head:
                key = self._candidate_key(item)
                analysis = analyses.get(key)
                if analysis is None:
                    continue
                item.score = float(analysis.final_score)
                if key == promoted_key and "strategy-bandit-safe-override" not in item.reasons:
                    item.reasons.insert(0, "strategy-bandit-safe-override")
            head.sort(
                key=lambda item: (
                    0 if self._candidate_key(item) == promoted_key else 1,
                    -float(analyses[self._candidate_key(item)].final_score),
                    *self._sort_key(item)[1:],
                )
            )
        else:
            for item in head:
                key = self._candidate_key(item)
                if key == baseline_key:
                    item.score = baseline_score
                    continue
                analysis = analyses.get(key)
                if analysis is None:
                    continue
                item.score = min(float(analysis.final_score), max(baseline_score - 0.001, 0.0))
            rest = sorted(head[1:], key=self._sort_key)
            head = [baseline] + rest

        return head + tail

    def rank(
        self,
        profile: QueryProfile,
        candidates: list[dict[str, Any]],
        *,
        project_scope: str,
        limit: int | None = None,
        use_strategy_overlay: bool = False,
    ) -> list[RankedCandidate]:
        preference_rules = (
            self.store.load_matching_preference_rules(profile=profile, project_scope=project_scope)
            if self.store is not None and self.settings is not None and self.settings.enable_preference_rules
            else []
        )
        error_family = getattr(profile, "error_family", "") or ""
        ranked = [
                self.score(
                    profile,
                    candidate,
                    project_scope=project_scope,
                    preference_rules=preference_rules,
                    error_family=error_family,
                )
            for candidate in candidates
        ]
        ranked.sort(key=self._sort_key)
        if use_strategy_overlay and self.strategy_bandit is not None:
            ranked = self._apply_strategy_bandit(profile, ranked)
        return ranked[:limit] if limit is not None else ranked
