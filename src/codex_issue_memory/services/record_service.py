from __future__ import annotations

from typing import Any

from ..matching import IssueMatcher
from ..retrieval import DenseEmbeddingIndex
from ..normalization import build_query_profile, derive_strategy_key, infer_strategy_hints, parse_tag_string
from ..storage import IssueMemoryStore
from ..security import sanitize_json_text, sanitize_text
from .consolidation_service import ConsolidationService


class RecordResolutionService:
    """Write verified fixes into the pattern/variant/episode memory model."""

    def __init__(self, store: IssueMemoryStore, matcher: IssueMatcher) -> None:
        self.store = store
        self.matcher = matcher
        self.consolidation_service = ConsolidationService(store, matcher)
        self.dense_index = DenseEmbeddingIndex(store)

    def record(
        self,
        *,
        title: str,
        raw_error: str,
        canonical_fix: str,
        prevention_rule: str,
        project_scope: str = "global",
        user_scope: str = "",
        canonical_symptom: str = "",
        verification_steps: str = "",
        tags: str = "",
        error_family: str = "auto",
        root_cause_class: str = "auto",
        context: str = "",
        file_path: str = "",
        command: str = "",
        domain: str = "generic",
        stack_excerpt: str = "",
        env_json: str = "",
        repo_name: str = "",
        git_commit: str = "",
        session_id: str = "",
        verification_command: str = "",
        verification_output: str = "",
        resolution_notes: str = "",
        patch_summary: str = "",
        patch_hash: str = "",
    ) -> dict[str, Any]:
        extra_tags = parse_tag_string(tags)
        sanitized_raw_error = sanitize_text(raw_error, enabled=self.store.settings.enable_redaction, max_chars=8000)
        sanitized_context = sanitize_text(context, enabled=self.store.settings.enable_redaction, max_chars=8000)
        sanitized_stack_excerpt = sanitize_text(stack_excerpt, enabled=self.store.settings.enable_redaction, max_chars=4000)
        sanitized_env_json = sanitize_json_text(
            env_json,
            enabled=self.store.settings.enable_redaction,
            max_chars=self.store.settings.env_json_max_chars,
        )
        sanitized_verification_output = sanitize_text(
            verification_output,
            enabled=self.store.settings.enable_redaction,
            max_chars=self.store.settings.verification_output_max_chars,
        )
        sanitized_resolution_notes = sanitize_text(
            resolution_notes,
            enabled=self.store.settings.enable_redaction,
            max_chars=self.store.settings.note_max_chars,
        )
        profile = build_query_profile(
            error_text=raw_error,
            context=context if context else canonical_symptom,
            command=command,
            file_path=file_path,
            stack_excerpt=stack_excerpt,
            env_json=env_json,
            repo_name=repo_name,
            git_commit=git_commit,
            hint_family=error_family,
            hint_root_cause=root_cause_class,
            extra_tags=extra_tags,
            project_scope=project_scope,
            user_scope=user_scope.strip() or self.store.settings.default_user_scope,
        )

        normalized_symptom = canonical_symptom.strip() or profile.normalized_text[:400]
        merged_tags = list(dict.fromkeys(profile.tags + extra_tags))
        resolution_strategy_hints = infer_strategy_hints(
            sanitized_raw_error,
            canonical_fix,
            prevention_rule,
            verification_steps,
            sanitized_resolution_notes,
            patch_summary,
            command,
            file_path,
        )
        strategy_hints = list(dict.fromkeys(profile.strategy_hints + resolution_strategy_hints))
        strategy_key = derive_strategy_key(
            canonical_fix,
            prevention_rule,
            verification_steps,
            sanitized_resolution_notes,
            patch_summary,
            command,
            file_path,
        )
        plan = self.consolidation_service.plan(
            profile=profile,
            title=title.strip(),
            project_scope=project_scope.strip() or "global",
            canonical_symptom=normalized_symptom,
            merged_tags=merged_tags,
            command=command,
            file_path=file_path,
            stack_excerpt=sanitized_stack_excerpt,
            env_json=sanitized_env_json,
            repo_name=repo_name,
            git_commit=git_commit,
            session_id=session_id,
        )

        result = self.store.record_resolution(
            matched_pattern_id=plan.matched_pattern_id,
            matched_variant_id=plan.matched_variant_id,
            pattern_payload={
                "title": title.strip(),
                "project_scope": project_scope.strip() or "global",
                "domain": domain.strip() or "generic",
                "error_family": profile.error_family,
                "root_cause_class": profile.root_cause_class,
                "canonical_symptom": normalized_symptom,
                "canonical_fix": canonical_fix.strip(),
                "prevention_rule": prevention_rule.strip(),
                "verification_steps": verification_steps.strip(),
                "tags": merged_tags,
                "signature": plan.pattern_signature,
                "confidence": 0.78,
            },
            variant_payload={
                "variant_key": plan.proposed_variant_key,
                "title": title.strip(),
                "canonical_fix": canonical_fix.strip(),
                "verification_steps": verification_steps.strip(),
                "rollback_steps": "",
                "tags": merged_tags,
                "repo_fingerprint": profile.repo_fingerprint,
                "env_fingerprint": profile.env_fingerprint,
                "command_signature": profile.command_signature,
                "file_path_signature": profile.path_signature,
                "stack_signature": profile.stack_signature,
                "patch_hash": patch_hash.strip(),
                "patch_summary": patch_summary.strip(),
                "confidence": 0.78,
                "memory_strength": 0.65,
                "status": "provisional" if plan.requires_review else "active",
                "strategy_key": strategy_key,
                "strategy_hints": strategy_hints,
                "entity_slots": profile.entity_slots,
                "applicability": {
                    "project_scope": project_scope,
                    "error_family": profile.error_family,
                    "root_cause_class": profile.root_cause_class,
                    "command": command,
                    "file_path": file_path,
                    "repo_name": repo_name,
                },
                "negative_applicability": {},
            },
            episode_payload={
                "session_id": session_id,
                "project_scope": project_scope,
                "user_scope": profile.user_scope,
                "repo_name": repo_name,
                "repo_fingerprint": profile.repo_fingerprint,
                "git_commit": git_commit,
                "source": "manual",
                "raw_error": sanitized_raw_error,
                "normalized_error": profile.normalized_text,
                "context": sanitized_context,
                "stack_excerpt": sanitized_stack_excerpt,
                "command": command,
                "file_path": file_path,
                "exception_types": profile.exception_types,
                "query_tokens": profile.tokens,
                "entity_slots": profile.entity_slots,
                "strategy_hints": strategy_hints,
                "env_fingerprint": profile.env_fingerprint,
                "env_json": sanitized_env_json,
                "patch_hash": patch_hash.strip(),
                "patch_summary": patch_summary.strip(),
                "verification_command": verification_command.strip(),
                "verification_output": sanitized_verification_output,
                "outcome": "verified",
                "consolidation_status": "review" if plan.requires_review else "attached",
                "resolution_notes": sanitized_resolution_notes,
            },
            example_payload={
                "raw_error": sanitized_raw_error,
                "normalized_error": profile.normalized_text,
                "context": sanitized_context,
                "file_path": file_path,
                "command": command,
                "verified_fix": canonical_fix,
            },
            review_payload=(
                {
                    "project_scope": project_scope,
                    "user_scope": profile.user_scope,
                    "repo_name": repo_name,
                    "strategy_key": strategy_key,
                    "review_reason": ", ".join(plan.reasons) if plan.reasons else plan.match_strategy,
                    "entity_slots": profile.entity_slots,
                    "metadata": {
                        "matched_pattern_id": plan.matched_pattern_id,
                        "matched_variant_id": plan.matched_variant_id,
                        "proposed_pattern_key": plan.proposed_pattern_key,
                        "proposed_variant_key": plan.proposed_variant_key,
                        "score": round(plan.consolidation_score, 4),
                        "reasons": plan.reasons,
                        "match_strategy": plan.match_strategy,
                        "variant_strategy": plan.variant_strategy,
                    },
                }
                if plan.requires_review
                else None
            ),
        )
        if self.store.settings.enable_dense_retrieval:
            self.dense_index.refresh_pattern(int(result["pattern_id"]))
            self.dense_index.refresh_variant(int(result["variant_id"]))

        result["match_strategy"] = plan.match_strategy
        result["variant_strategy"] = plan.variant_strategy
        result["consolidation"] = {
            "matched_pattern_id": plan.matched_pattern_id,
            "matched_variant_id": plan.matched_variant_id,
            "pattern_key": plan.pattern_signature,
            "proposed_variant_key": plan.proposed_variant_key,
            "score": round(plan.consolidation_score, 4),
            "requires_review": plan.requires_review,
            "reasons": plan.reasons,
        }
        result["query_profile"] = {
            "error_family": profile.error_family,
            "root_cause_class": profile.root_cause_class,
            "pattern_key": plan.pattern_signature,
            "variant_key": plan.proposed_variant_key,
            "strategy_key": strategy_key,
            "strategy_hints": strategy_hints,
            "entity_slots": profile.entity_slots,
            "user_scope": profile.user_scope,
        }
        result["note"] = (
            "Resolution stored atomically as pattern + variant + episode. "
            "Consolidation now uses variant-first retrieval with feedback-weighted merge decisions. "
            "Use issue_get to inspect context-specific variants before reusing a fix."
        )
        return result
