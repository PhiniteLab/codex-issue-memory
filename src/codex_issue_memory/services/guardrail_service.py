from __future__ import annotations

from collections import defaultdict
from typing import Any

from ..matching import IssueMatcher
from ..normalization import build_query_profile
from ..storage import IssueMemoryStore


class GuardrailService:
    """Build proactive guardrails from stored patterns and explicit user preferences."""

    def __init__(self, store: IssueMemoryStore, matcher: IssueMatcher) -> None:
        self.store = store
        self.matcher = matcher

    @staticmethod
    def _truncate(text: str, limit: int) -> str:
        normalized = text.strip()
        if len(normalized) <= limit:
            return normalized
        return normalized[: limit - 1].rstrip() + "…"

    def plan(
        self,
        *,
        error_text: str = "",
        context: str = "",
        command: str = "",
        file_path: str = "",
        repo_name: str = "",
        project_scope: str = "global",
        user_scope: str = "",
        limit: int = 5,
    ) -> dict[str, Any]:
        resolved_user_scope = user_scope.strip() or self.store.settings.default_user_scope
        normalized_project_scope = project_scope.strip() or "global"
        seed_text = error_text.strip() or context.strip() or command.strip() or file_path.strip() or repo_name.strip() or "proactive guardrail scan"
        profile = build_query_profile(
            error_text=seed_text,
            context=context,
            command=command,
            file_path=file_path,
            repo_name=repo_name,
            project_scope=normalized_project_scope,
            user_scope=resolved_user_scope,
        )
        ranked = self.matcher.ranked_candidates(
            profile,
            project_scope=normalized_project_scope,
            limit=max(limit * 2, self.store.settings.guardrail_limit),
            repo_name=repo_name,
            retrieval_context="guardrail",
        )
        preference_rules = (
            self.store.load_matching_preference_rules(profile=profile, project_scope=normalized_project_scope, limit=max(limit * 2, 10))
            if self.store.settings.enable_preference_rules
            else []
        )

        preferred: dict[str, float] = defaultdict(float)
        avoided: dict[str, float] = defaultdict(float)
        for rule in preference_rules:
            strategy_key = str(rule.get("strategy_key", "")).strip()
            if not strategy_key:
                continue
            contribution = float(rule.get("weight", 0.0)) * float(rule.get("match_score", 0.0))
            if contribution >= 0.0:
                preferred[strategy_key] += contribution
            else:
                avoided[strategy_key] += abs(contribution)

        guardrails: list[dict[str, Any]] = []
        seen_pairs: set[tuple[int, int]] = set()
        for item in ranked:
            candidate = item.candidate
            variant = candidate.get("best_variant") or {}
            pattern_id = int(candidate.get("pattern_id", candidate.get("id", 0)) or 0)
            variant_id = int(candidate.get("variant_id") or variant.get("id") or 0)
            pair = (pattern_id, variant_id)
            if pair in seen_pairs:
                continue
            seen_pairs.add(pair)
            strategy_key = str(variant.get("strategy_key", "")).strip()
            prevention_rule = str(candidate.get("prevention_rule", "")).strip()
            verification_steps = str(variant.get("verification_steps") or candidate.get("verification_steps", "")).strip()
            canonical_fix = str(variant.get("canonical_fix") or candidate.get("canonical_fix", "")).strip()
            if not prevention_rule and not verification_steps and not canonical_fix:
                continue
            preference_alignment = round(preferred.get(strategy_key, 0.0) - avoided.get(strategy_key, 0.0), 4)
            guardrails.append(
                {
                    "pattern_id": pattern_id,
                    "variant_id": variant_id or None,
                    "title": str(variant.get("title") or candidate.get("title", "")),
                    "score": round(float(item.score), 3),
                    "strategy_key": strategy_key,
                    "canonical_fix": self._truncate(canonical_fix, 180),
                    "prevention_rule": self._truncate(prevention_rule, 180),
                    "verification_steps": self._truncate(verification_steps, 180),
                    "preference_alignment": preference_alignment,
                    "why": item.reasons[:6],
                }
            )
            if len(guardrails) >= max(limit, 1):
                break

        matched_rules = [
            {
                "rule_id": int(rule["id"]),
                "scope_type": str(rule["scope_type"]),
                "scope_key": str(rule["scope_key"]),
                "project_scope": str(rule["project_scope"]),
                "repo_name": str(rule.get("repo_name", "")),
                "error_family": str(rule.get("error_family", "")),
                "strategy_key": str(rule.get("strategy_key", "")),
                "weight": round(float(rule.get("weight", 0.0)), 4),
                "match_score": round(float(rule.get("match_score", 0.0)), 4),
                "instruction": self._truncate(str(rule.get("instruction", "")), 140),
            }
            for rule in preference_rules[: max(limit, 1)]
        ]
        preferred_strategies = [
            {"strategy_key": key, "score": round(value, 4)}
            for key, value in sorted(preferred.items(), key=lambda item: (-item[1], item[0]))[: max(limit, 1)]
        ]
        avoided_strategies = [
            {"strategy_key": key, "score": round(value, 4)}
            for key, value in sorted(avoided.items(), key=lambda item: (-item[1], item[0]))[: max(limit, 1)]
        ]

        if guardrails:
            next_action = (
                "Apply the listed prevention rules and preferred strategies before editing. "
                "When a concrete failure appears, call issue_match to retrieve the exact stored fix."
            )
        elif matched_rules:
            next_action = "No concrete guardrail pattern matched, but stored user preferences can still bias future issue_match calls."
        else:
            next_action = "No guardrails surfaced yet. Record verified fixes and explicit preferences so the memory can become proactive."

        return {
            "query_profile": {
                "project_scope": profile.project_scope,
                "user_scope": profile.user_scope,
                "repo_name": profile.repo_name,
                "error_family": profile.error_family,
                "root_cause_class": profile.root_cause_class,
                "strategy_hints": profile.strategy_hints,
                "entity_slots": profile.entity_slots,
            },
            "preferences": {
                "matched_rules": matched_rules,
                "preferred_strategies": preferred_strategies,
                "avoided_strategies": avoided_strategies,
            },
            "guardrails": guardrails,
            "next_action": next_action,
        }
