from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..matching import IssueMatcher
from ..models import QueryProfile
from ..normalization import compare_entity_slots, make_pattern_key, make_variant_key, tokenize
from ..storage import IssueMemoryStore


@dataclass(slots=True)
class ConsolidationPlan:
    matched_pattern_id: int | None
    matched_variant_id: int | None
    pattern_signature: str
    proposed_pattern_key: str
    proposed_variant_key: str
    match_strategy: str
    variant_strategy: str
    consolidation_score: float = 0.0
    reasons: list[str] = field(default_factory=list)
    requires_review: bool = False


class ConsolidationService:
    """Choose whether a new verified resolution should attach to an existing pattern/variant."""

    PATTERN_ATTACH_THRESHOLD = 0.74
    VARIANT_ATTACH_THRESHOLD = 0.84
    EXACT_PATTERN_VARIANT_THRESHOLD = 0.33
    REVIEW_THRESHOLD = 0.66

    def __init__(self, store: IssueMemoryStore, matcher: IssueMatcher) -> None:
        self.store = store
        self.matcher = matcher

    def plan(
        self,
        *,
        profile: QueryProfile,
        title: str,
        project_scope: str,
        canonical_symptom: str,
        merged_tags: list[str],
        command: str,
        file_path: str,
        stack_excerpt: str,
        env_json: str,
        repo_name: str,
        git_commit: str,
        session_id: str,
    ) -> ConsolidationPlan:
        proposed_pattern_key = make_pattern_key(
            title=title,
            project_scope=project_scope,
            error_family=profile.error_family,
            root_cause_class=profile.root_cause_class,
            canonical_symptom=canonical_symptom,
            tags=merged_tags,
        )
        proposed_variant_key = make_variant_key(
            pattern_key=proposed_pattern_key,
            command=command,
            file_path=file_path,
            stack_excerpt=stack_excerpt,
            env_json=env_json,
            repo_name=repo_name,
            git_commit=git_commit,
        )

        existing_pattern = self.store.find_pattern_by_signature(project_scope, proposed_pattern_key)
        if existing_pattern is not None:
            pattern_id = int(existing_pattern["id"])
            pattern_signature = str(existing_pattern["signature"])
            exact_variant = self.store.find_variant_by_key(pattern_id=pattern_id, variant_key=proposed_variant_key)
            if exact_variant is not None:
                return ConsolidationPlan(
                    matched_pattern_id=pattern_id,
                    matched_variant_id=int(exact_variant["id"]),
                    pattern_signature=pattern_signature,
                    proposed_pattern_key=proposed_pattern_key,
                    proposed_variant_key=proposed_variant_key,
                    match_strategy="exact_pattern_key",
                    variant_strategy="exact_variant_key",
                    consolidation_score=1.0,
                    reasons=["exact-pattern-key", "exact-variant-key"],
                )
            best_variant_id, best_score, reasons = self._best_variant_for_pattern(
                pattern_id=pattern_id,
                profile=profile,
                proposed_variant_key=proposed_variant_key,
            )
            return ConsolidationPlan(
                matched_pattern_id=pattern_id,
                matched_variant_id=best_variant_id if best_score >= self.EXACT_PATTERN_VARIANT_THRESHOLD else None,
                pattern_signature=pattern_signature,
                proposed_pattern_key=proposed_pattern_key,
                proposed_variant_key=proposed_variant_key,
                match_strategy="exact_pattern_key",
                variant_strategy=(
                    "feedback_weighted_variant_merge"
                    if best_variant_id is not None and best_score >= self.EXACT_PATTERN_VARIANT_THRESHOLD
                    else "new_variant_in_exact_pattern"
                ),
                consolidation_score=round(best_score, 4),
                reasons=["exact-pattern-key", *reasons],
                requires_review=best_variant_id is not None and (self.EXACT_PATTERN_VARIANT_THRESHOLD - 0.08) <= best_score < self.EXACT_PATTERN_VARIANT_THRESHOLD,
            )

        ranked = self.matcher.ranked_candidates(
            profile,
            project_scope=project_scope,
            limit=6,
            session_id=session_id,
            repo_name=repo_name,
            retrieval_context="consolidation",
        )
        for item in ranked:
            candidate = item.candidate
            if candidate.get("project_scope") not in {project_scope, "global"}:
                continue
            if profile.error_family != "generic_runtime_error" and candidate.get("error_family") != profile.error_family:
                continue
            if profile.root_cause_class != "unknown" and candidate.get("root_cause_class") != profile.root_cause_class:
                continue
            pattern_id = int(candidate.get("pattern_id", candidate["id"]))
            pattern = self.store.find_pattern_by_id(pattern_id)
            if pattern is None:
                continue
            pattern_signature = str(pattern["signature"])
            candidate_variant = candidate.get("best_variant") or {}
            variant_id = int(candidate_variant["id"]) if isinstance(candidate_variant, dict) and candidate_variant.get("id") is not None else None
            variant_score = 0.0
            reasons = list(item.reasons)
            if variant_id is not None:
                variant_score, variant_reasons = self._variant_similarity(profile, candidate_variant, proposed_variant_key=proposed_variant_key)
                reasons.extend(variant_reasons)
            if item.score >= self.PATTERN_ATTACH_THRESHOLD:
                attach_variant = variant_id if variant_id is not None and variant_score >= self.VARIANT_ATTACH_THRESHOLD else None
                requires_review = attach_variant is not None and self.REVIEW_THRESHOLD <= item.score < self.PATTERN_ATTACH_THRESHOLD
                return ConsolidationPlan(
                    matched_pattern_id=pattern_id,
                    matched_variant_id=attach_variant,
                    pattern_signature=pattern_signature,
                    proposed_pattern_key=proposed_pattern_key,
                    proposed_variant_key=proposed_variant_key,
                    match_strategy="retrieval_merge",
                    variant_strategy=("retrieval_variant_merge" if attach_variant is not None else "retrieval_new_variant"),
                    consolidation_score=round(max(item.score, variant_score), 4),
                    reasons=reasons[:12],
                    requires_review=requires_review,
                )
            break

        return ConsolidationPlan(
            matched_pattern_id=None,
            matched_variant_id=None,
            pattern_signature=proposed_pattern_key,
            proposed_pattern_key=proposed_pattern_key,
            proposed_variant_key=proposed_variant_key,
            match_strategy="new_pattern",
            variant_strategy="new_variant_new_pattern",
            consolidation_score=0.0,
            reasons=["no-safe-consolidation-hit"],
        )

    def _best_variant_for_pattern(
        self,
        *,
        pattern_id: int,
        profile: QueryProfile,
        proposed_variant_key: str,
    ) -> tuple[int | None, float, list[str]]:
        best_variant_id: int | None = None
        best_score = 0.0
        best_reasons: list[str] = []
        for variant in self.store.get_variants_for_pattern(pattern_id, limit=50):
            score, reasons = self._variant_similarity(profile, variant, proposed_variant_key=proposed_variant_key)
            if score > best_score:
                best_variant_id = int(variant["id"])
                best_score = score
                best_reasons = reasons
        return best_variant_id, best_score, best_reasons

    def _variant_similarity(
        self,
        profile: QueryProfile,
        variant: dict[str, Any],
        *,
        proposed_variant_key: str,
    ) -> tuple[float, list[str]]:
        reasons: list[str] = []
        score = 0.0
        if str(variant.get("variant_key", "")) == proposed_variant_key:
            return 1.0, ["exact-variant-key"]
        if profile.command_signature and variant.get("command_signature") == profile.command_signature:
            score += 0.24
            reasons.append("command-signature-match")
        if profile.path_signature and variant.get("file_path_signature") == profile.path_signature:
            score += 0.20
            reasons.append("path-signature-match")
        if profile.stack_signature and variant.get("stack_signature") == profile.stack_signature:
            score += 0.28
            reasons.append("stack-signature-match")
        if profile.env_fingerprint and variant.get("env_fingerprint") == profile.env_fingerprint:
            score += 0.12
            reasons.append("env-match")
        if profile.repo_fingerprint and variant.get("repo_fingerprint") == profile.repo_fingerprint:
            score += 0.08
            reasons.append("repo-match")

        variant_tokens = tokenize(
            " ".join(
                [
                    str(variant.get("title", "")),
                    str(variant.get("canonical_fix", "")),
                    str(variant.get("verification_steps", "")),
                    str(variant.get("patch_summary", "")),
                    " ".join(str(tag) for tag in variant.get("tags_json", []) if isinstance(variant.get("tags_json", []), list)),
                ]
            ),
            max_tokens=96,
        )
        if profile.tokens and variant_tokens:
            overlap = len(set(profile.tokens) & set(variant_tokens)) / max(1, len(set(profile.tokens) | set(variant_tokens)))
            score += min(overlap, 1.0) * 0.18
            if overlap > 0:
                reasons.append("lexical-overlap")

        entity_slots = variant.get("entity_slots_json") if isinstance(variant.get("entity_slots_json"), dict) else {}
        entity_signals = compare_entity_slots(profile.entity_slots, entity_slots)
        entity_match = float(entity_signals.get("match_score", 0.0))
        entity_penalty = float(entity_signals.get("conflict_penalty", 0.0))
        if entity_match > 0:
            score += min(entity_match, 1.0) * 0.35
        if entity_penalty > 0:
            score -= min(entity_penalty, 1.0) * 0.55
        reasons.extend(str(reason) for reason in entity_signals.get("reasons", []))

        strategy_key = str(variant.get("strategy_key", "")).strip()
        if strategy_key and profile.strategy_hints and strategy_key in profile.strategy_hints:
            score += 0.08
            reasons.append("strategy-hint-match")

        success_count = int(variant.get("success_count", 0))
        reject_count = int(variant.get("reject_count", 0))
        confidence = float(variant.get("confidence", 0.5))
        memory_strength = float(variant.get("memory_strength", 0.5))
        success_ratio = (success_count + 1.0) / max(success_count + reject_count + 2.0, 1.0)
        reject_ratio = reject_count / max(success_count + reject_count + 1.0, 1.0)
        feedback_weight = max(0.0, 0.40 * confidence + 0.35 * memory_strength + 0.25 * success_ratio - 0.20 * reject_ratio)
        feedback_bonus = max(0.0, min(feedback_weight, 1.0) - 0.72) * 0.85
        score += feedback_bonus
        if feedback_bonus > 0:
            reasons.append("feedback-weight")
        return min(max(score, 0.0), 1.0), reasons[:10]
