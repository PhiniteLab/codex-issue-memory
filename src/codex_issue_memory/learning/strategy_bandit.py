from __future__ import annotations

from dataclasses import dataclass
from math import sqrt
from typing import Any

from ..models import QueryProfile
from ..settings import Settings
from ..storage import (
    STRATEGY_PRIOR_ALPHA,
    STRATEGY_PRIOR_BETA,
    VARIANT_PRIOR_ALPHA,
    VARIANT_PRIOR_BETA,
    IssueMemoryStore,
)
from .families import resolve_strategy_family
from .posteriors import BetaPosterior, build_beta_posterior, shrinkage_weight


_SCOPE_LAMBDAS = {
    "global": 6.0,
    "repo": 3.5,
    "user": 2.5,
}


@dataclass(slots=True)
class StrategyBanditOutcome:
    candidate_key: str
    strategy_key: str
    strategy_mean: float
    strategy_sample: float
    strategy_std: float
    variant_mean: float
    variant_sample: float
    variant_std: float
    effective_evidence: float
    negative_penalty: float
    fp_count: int
    adjustment: float
    final_score: float
    conservative_score: float
    reasons: list[str]
    quality_mean: float = 0.5
    safety_mean: float = 0.5
    adoption_mean: float = 0.5


class StrategyThompsonBandit:
    """Conservative hierarchical Thompson-style overlay over top retrieval candidates."""

    def __init__(self, store: IssueMemoryStore, settings: Settings) -> None:
        self.store = store
        self.settings = settings

    @staticmethod
    def candidate_key(candidate: dict[str, Any]) -> str:
        pattern_id = int(candidate.get("pattern_id", candidate.get("id", 0)) or 0)
        variant = candidate.get("best_variant") or {}
        variant_id = int(candidate.get("variant_id") or variant.get("id") or 0)
        return f"{pattern_id}:{variant_id}"

    @staticmethod
    def _clamp(value: float, *, low: float = 0.0, high: float = 0.999) -> float:
        return min(max(float(value), low), high)

    @staticmethod
    def _center(value: float) -> float:
        return max(min((float(value) - 0.5) * 2.0, 1.0), -1.0)

    @staticmethod
    def _scope_signal(posterior: BetaPosterior, *, scope_name: str) -> float:
        exploration = (
            0.30 if scope_name == "user" else 0.25 if scope_name == "repo" else 0.20
        )
        return (1.0 - exploration) * posterior.mean + exploration * posterior.sample

    @staticmethod
    def _weighted_std(
        weights: dict[str, float], posteriors: dict[str, BetaPosterior]
    ) -> float:
        variance = 0.0
        for name, weight in weights.items():
            variance += (float(weight) ** 2) * (float(posteriors[name].std) ** 2)
        return sqrt(max(variance, 0.0))

    def _load_posterior(
        self,
        row: dict[str, Any] | None,
        *,
        prior_alpha: float,
        prior_beta: float,
        half_life_days: int,
        seed_parts: tuple[Any, ...],
        velocity_multiplier: float = 1.0,
    ) -> BetaPosterior:
        alpha = (
            float(row.get("alpha", prior_alpha))
            if row is not None
            else float(prior_alpha)
        )
        beta = (
            float(row.get("beta", prior_beta)) if row is not None else float(prior_beta)
        )
        updated_at = str(row.get("updated_at", "")) if row is not None else ""
        return build_beta_posterior(
            alpha=alpha,
            beta=beta,
            updated_at=updated_at,
            half_life_days=half_life_days,
            prior_alpha=prior_alpha,
            prior_beta=prior_beta,
            seed_parts=seed_parts,
            velocity_multiplier=velocity_multiplier,
        )

    def _combined_strategy_signal(
        self,
        *,
        global_posterior: BetaPosterior,
        repo_posterior: BetaPosterior,
        user_posterior: BetaPosterior,
    ) -> tuple[float, float, float]:
        raw_weights = {
            "global": shrinkage_weight(
                global_posterior.effective_observations,
                lambda_value=_SCOPE_LAMBDAS["global"],
            ),
            "repo": shrinkage_weight(
                repo_posterior.effective_observations,
                lambda_value=_SCOPE_LAMBDAS["repo"],
            ),
            "user": shrinkage_weight(
                user_posterior.effective_observations,
                lambda_value=_SCOPE_LAMBDAS["user"],
            ),
        }
        if sum(raw_weights.values()) <= 1e-9:
            weights = {"global": 1.0}
        else:
            total = sum(raw_weights.values())
            weights = {
                name: value / total
                for name, value in raw_weights.items()
                if value > 0.0
            }
        posteriors = {
            "global": global_posterior,
            "repo": repo_posterior,
            "user": user_posterior,
        }
        mean = 0.0
        sample = 0.0
        for name, weight in weights.items():
            posterior = posteriors[name]
            mean += weight * posterior.mean
            sample += weight * self._scope_signal(posterior, scope_name=name)
        std = self._weighted_std(weights, posteriors)
        return mean, sample, std

    @staticmethod
    def _match_any(value: str, items: Any) -> bool:
        if not value or not isinstance(items, list):
            return False
        normalized = value.strip().lower()
        return normalized in {
            str(item).strip().lower() for item in items if str(item).strip()
        }

    def _load_factor_posterior(
        self,
        row: dict[str, Any] | None,
        *,
        factor: str,
        half_life_days: int,
        seed_parts: tuple[Any, ...],
        velocity_multiplier: float = 1.0,
    ) -> BetaPosterior:
        alpha = (
            float(row.get(f"{factor}_alpha", STRATEGY_PRIOR_ALPHA))
            if row is not None
            else float(STRATEGY_PRIOR_ALPHA)
        )
        beta = (
            float(row.get(f"{factor}_beta", STRATEGY_PRIOR_BETA))
            if row is not None
            else float(STRATEGY_PRIOR_BETA)
        )
        updated_at = str(row.get("updated_at", "")) if row is not None else ""
        return build_beta_posterior(
            alpha=alpha,
            beta=beta,
            updated_at=updated_at,
            half_life_days=half_life_days,
            prior_alpha=STRATEGY_PRIOR_ALPHA,
            prior_beta=STRATEGY_PRIOR_BETA,
            seed_parts=seed_parts + (factor,),
            velocity_multiplier=velocity_multiplier,
        )

    def _composite_multi_factor_score(
        self,
        *,
        quality: BetaPosterior,
        safety: BetaPosterior,
        adoption: BetaPosterior,
    ) -> tuple[float, float, float]:
        """Compute quality × safety × adoption^0.5 as composite mean/sample/std."""
        q_signal = 0.70 * quality.mean + 0.30 * quality.sample
        s_signal = 0.70 * safety.mean + 0.30 * safety.sample
        a_signal = 0.70 * adoption.mean + 0.30 * adoption.sample
        composite_signal = q_signal * s_signal * (a_signal**0.5)
        composite_mean = quality.mean * safety.mean * (adoption.mean**0.5)
        composite_std = max(quality.std, safety.std, adoption.std)
        return composite_mean, composite_signal, composite_std

    def _negative_applicability_penalty(
        self, profile: QueryProfile, variant: dict[str, Any]
    ) -> tuple[float, list[str], int]:
        payload = variant.get("negative_applicability_json")
        if not isinstance(payload, dict) or not payload:
            return 0.0, [], 0
        fp_count = int(payload.get("false_positive_count", 0))
        penalty = min(float(fp_count) * 0.03, 0.15)
        reasons: list[str] = []
        if self._match_any(profile.project_scope, payload.get("project_scopes")):
            penalty += 0.08
            reasons.append("negative-applicability-project-scope")
        if profile.user_scope and self._match_any(
            profile.user_scope, payload.get("user_scopes")
        ):
            penalty += 0.08
            reasons.append("negative-applicability-user-scope")
        if profile.repo_name and self._match_any(
            profile.repo_name, payload.get("repo_names")
        ):
            penalty += 0.12
            reasons.append("negative-applicability-repo-name")
        command_value = " ".join(profile.command_tokens).strip().lower()
        if command_value and self._match_any(command_value, payload.get("commands")):
            penalty += 0.05
            reasons.append("negative-applicability-command")
        path_value = " ".join(profile.path_tokens).strip().lower()
        if path_value and self._match_any(path_value, payload.get("file_paths")):
            penalty += 0.05
            reasons.append("negative-applicability-file-path")
        return min(penalty, 0.30), reasons, fp_count

    def score_candidates(
        self,
        profile: QueryProfile,
        ranked_items: list[Any],
        *,
        project_scope: str,
    ) -> dict[str, StrategyBanditOutcome]:
        del (
            project_scope
        )  # reserved for future scope-aware priors without changing signature
        if not ranked_items:
            return {}

        candidates = [item.candidate for item in ranked_items]
        strategy_keys = []
        variant_ids = []
        for candidate in candidates:
            variant = candidate.get("best_variant") or {}
            strategy_key = str(variant.get("strategy_key", "")).strip()
            if strategy_key:
                strategy_keys.append(strategy_key)
            variant_id = int(candidate.get("variant_id") or variant.get("id") or 0)
            if variant_id > 0:
                variant_ids.append(variant_id)

        snapshot = self.store.load_strategy_bandit_stats(
            strategy_keys=strategy_keys,
            variant_ids=variant_ids,
            repo_name=profile.repo_name,
            user_scope=profile.user_scope,
        )

        # Load family-level priors for hierarchical shrinkage (Phase 3.2)
        family_keys = list(
            {
                resolve_strategy_family(sk)
                for sk in strategy_keys
                if resolve_strategy_family(sk)
            }
        )
        family_stats = self.store.load_family_stats(family_keys) if family_keys else {}

        velocity = self.store.query_repo_feedback_velocity(profile.repo_name)

        results: dict[str, StrategyBanditOutcome] = {}
        for item in ranked_items:
            candidate = item.candidate
            variant = candidate.get("best_variant") or {}
            strategy_key = str(variant.get("strategy_key", "")).strip()
            variant_id = int(candidate.get("variant_id") or variant.get("id") or 0)
            pattern_id = int(candidate.get("pattern_id", candidate.get("id", 0)) or 0)
            candidate_key = self.candidate_key(candidate)
            seed_base = (
                profile.normalized_text,
                profile.project_scope,
                profile.user_scope,
                profile.repo_name,
                pattern_id,
                variant_id,
                strategy_key,
            )

            global_posterior = self._load_posterior(
                snapshot.get("global", {}).get(strategy_key),
                prior_alpha=STRATEGY_PRIOR_ALPHA,
                prior_beta=STRATEGY_PRIOR_BETA,
                half_life_days=self.settings.strategy_half_life_days,
                seed_parts=seed_base + ("global",),
                velocity_multiplier=velocity,
            )
            repo_posterior = self._load_posterior(
                snapshot.get("repo", {}).get(strategy_key),
                prior_alpha=STRATEGY_PRIOR_ALPHA,
                prior_beta=STRATEGY_PRIOR_BETA,
                half_life_days=self.settings.strategy_half_life_days,
                seed_parts=seed_base + ("repo", profile.repo_name),
                velocity_multiplier=velocity,
            )
            user_posterior = self._load_posterior(
                snapshot.get("user", {}).get(strategy_key),
                prior_alpha=STRATEGY_PRIOR_ALPHA,
                prior_beta=STRATEGY_PRIOR_BETA,
                half_life_days=self.settings.strategy_half_life_days,
                seed_parts=seed_base + ("user", profile.user_scope),
                velocity_multiplier=velocity,
            )
            variant_posterior = self._load_posterior(
                snapshot.get("variants", {}).get(variant_id),
                prior_alpha=VARIANT_PRIOR_ALPHA,
                prior_beta=VARIANT_PRIOR_BETA,
                half_life_days=self.settings.variant_half_life_days,
                seed_parts=seed_base + ("variant",),
                velocity_multiplier=velocity,
            )

            strategy_mean, strategy_sample, strategy_std = (
                self._combined_strategy_signal(
                    global_posterior=global_posterior,
                    repo_posterior=repo_posterior,
                    user_posterior=user_posterior,
                )
            )

            # Multi-factor posteriors (Phase 3.1) — use global row for factor data
            global_row = snapshot.get("global", {}).get(strategy_key)
            quality_post = self._load_factor_posterior(
                global_row,
                factor="quality",
                half_life_days=self.settings.strategy_half_life_days,
                seed_parts=seed_base + ("quality",),
                velocity_multiplier=velocity,
            )
            safety_post = self._load_factor_posterior(
                global_row,
                factor="safety",
                half_life_days=self.settings.strategy_half_life_days,
                seed_parts=seed_base + ("safety",),
                velocity_multiplier=velocity,
            )
            adoption_post = self._load_factor_posterior(
                global_row,
                factor="adoption",
                half_life_days=self.settings.strategy_half_life_days,
                seed_parts=seed_base + ("adoption",),
                velocity_multiplier=velocity,
            )

            # Family hierarchical shrinkage (Phase 3.2)
            fam_key = resolve_strategy_family(strategy_key)
            fam_row = family_stats.get(fam_key) if fam_key else None
            if fam_row is not None:
                fam_quality = self._load_factor_posterior(
                    fam_row,
                    factor="quality",
                    half_life_days=self.settings.strategy_half_life_days,
                    seed_parts=seed_base + ("family", "quality"),
                    velocity_multiplier=velocity,
                )
                fam_safety = self._load_factor_posterior(
                    fam_row,
                    factor="safety",
                    half_life_days=self.settings.strategy_half_life_days,
                    seed_parts=seed_base + ("family", "safety"),
                    velocity_multiplier=velocity,
                )
                fam_adoption = self._load_factor_posterior(
                    fam_row,
                    factor="adoption",
                    half_life_days=self.settings.strategy_half_life_days,
                    seed_parts=seed_base + ("family", "adoption"),
                    velocity_multiplier=velocity,
                )
                _FAMILY_LAMBDA = 4.0
                for strat_post, fam_post in [
                    (quality_post, fam_quality),
                    (safety_post, fam_safety),
                    (adoption_post, fam_adoption),
                ]:
                    sw = shrinkage_weight(
                        strat_post.effective_observations, lambda_value=_FAMILY_LAMBDA
                    )
                    blended_mean = sw * strat_post.mean + (1.0 - sw) * fam_post.mean
                    blended_sample = (
                        sw * strat_post.sample + (1.0 - sw) * fam_post.sample
                    )
                    # Mutate in-place via object.__setattr__ on slots
                    object.__setattr__(strat_post, "mean", blended_mean)
                    object.__setattr__(strat_post, "sample", blended_sample)

            composite_mean, composite_signal, _composite_std = (
                self._composite_multi_factor_score(
                    quality=quality_post,
                    safety=safety_post,
                    adoption=adoption_post,
                )
            )
            # Blend single-factor signal with multi-factor composite
            factor_evidence = max(
                quality_post.effective_observations,
                safety_post.effective_observations,
                adoption_post.effective_observations,
            )
            factor_blend = min(
                factor_evidence / 8.0, 1.0
            )  # ramp to full weight over ~8 obs
            strategy_mean = (
                1.0 - factor_blend
            ) * strategy_mean + factor_blend * composite_mean
            strategy_sample = (
                1.0 - factor_blend
            ) * strategy_sample + factor_blend * composite_signal

            if strategy_key in {"", "general_reusable_fix"}:
                strategy_mean = 0.5
                strategy_sample = 0.5
                strategy_std = 0.0
                strategy_effective = 0.0
            else:
                strategy_effective = max(
                    global_posterior.effective_observations,
                    repo_posterior.effective_observations,
                    user_posterior.effective_observations,
                )
            variant_signal = (
                0.65 * variant_posterior.mean + 0.35 * variant_posterior.sample
            )

            negative_penalty, negative_reasons, fp_count = (
                self._negative_applicability_penalty(profile, variant)
            )

            effective_evidence = (
                strategy_effective + 0.5 * variant_posterior.effective_observations
            )
            evidence_scale = min(
                effective_evidence
                / max(float(self.settings.minimum_strategy_evidence), 1.0),
                1.0,
            )

            strategy_adjustment = (
                self.settings.strategy_overlay_scale
                * self._center(strategy_sample)
                * evidence_scale
            )
            variant_adjustment = (
                self.settings.variant_overlay_scale
                * self._center(variant_signal)
                * evidence_scale
            )
            adjustment = strategy_adjustment + variant_adjustment - negative_penalty

            base_score = float(item.score)
            final_score = self._clamp(base_score + adjustment)
            conservative_strategy = strategy_mean
            conservative_variant = variant_posterior.mean
            conservative_score = self._clamp(
                base_score
                + self.settings.strategy_overlay_scale
                * self._center(conservative_strategy)
                * evidence_scale
                + self.settings.variant_overlay_scale
                * self._center(conservative_variant)
                * evidence_scale
                - negative_penalty
            )

            reasons: list[str] = []
            if strategy_key and strategy_key != "general_reusable_fix":
                reasons.append(f"strategy-preference:{strategy_key}")
            if strategy_adjustment > 1e-3:
                reasons.append("strategy-bandit-exploitation")
            if variant_adjustment > 1e-3 and variant_id > 0:
                reasons.append("strategy-bandit-variant-residual")
            if negative_penalty > 1e-6:
                reasons.extend(negative_reasons or ["negative-applicability-penalty"])

            results[candidate_key] = StrategyBanditOutcome(
                candidate_key=candidate_key,
                strategy_key=strategy_key,
                strategy_mean=strategy_mean,
                strategy_sample=strategy_sample,
                strategy_std=strategy_std,
                variant_mean=variant_posterior.mean,
                variant_sample=variant_signal,
                variant_std=variant_posterior.std,
                effective_evidence=effective_evidence,
                negative_penalty=negative_penalty,
                fp_count=fp_count,
                adjustment=adjustment,
                final_score=final_score,
                conservative_score=conservative_score,
                reasons=reasons,
                quality_mean=quality_post.mean,
                safety_mean=safety_post.mean,
                adoption_mean=adoption_post.mean,
            )
        return results


__all__ = ["StrategyBanditOutcome", "StrategyThompsonBandit"]
