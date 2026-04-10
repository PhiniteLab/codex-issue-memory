from __future__ import annotations

from typing import Any

from .matching import IssueMatcher
from .normalization import build_query_profile
from .services import FeedbackService, GuardrailService, PreferenceService, RecordResolutionService, SessionService
from .storage import IssueMemoryStore


class IssueMemoryApp:
    """High-level application service for matching and writing issue memory."""

    def __init__(self, store: IssueMemoryStore | None = None) -> None:
        self.store = store or IssueMemoryStore.from_env()
        self.store.initialize()
        self.matcher = IssueMatcher(self.store, settings=self.store.settings)
        self.session_service = SessionService(self.store)
        self.record_service = RecordResolutionService(self.store, self.matcher)
        self.preference_service = PreferenceService(self.store)
        self.feedback_service = FeedbackService(self.store, self.session_service, self.preference_service)
        self.guardrail_service = GuardrailService(self.store, self.matcher)

    def issue_match(
        self,
        *,
        error_text: str,
        context: str = "",
        command: str = "",
        file_path: str = "",
        stack_excerpt: str = "",
        env_json: str = "",
        repo_name: str = "",
        git_commit: str = "",
        session_id: str = "",
        project_scope: str = "global",
        user_scope: str = "",
        limit: int = 3,
    ) -> dict[str, Any]:
        resolved_user_scope = user_scope.strip() or self.store.settings.default_user_scope
        profile = build_query_profile(
            error_text=error_text,
            context=context,
            command=command,
            file_path=file_path,
            stack_excerpt=stack_excerpt,
            env_json=env_json,
            repo_name=repo_name,
            git_commit=git_commit,
            project_scope=project_scope,
            user_scope=resolved_user_scope,
        )
        matches, decision, event_meta = self.matcher.match_with_decision(
            profile,
            project_scope=project_scope,
            limit=limit,
            session_id=session_id,
            repo_name=repo_name,
            log_event=True,
        )
        if decision.status == "match":
            next_action = (
                "Inspect the top match first. Call issue_get if you need stored variants, "
                "episodes, or full verification steps. After trying the suggested fix, "
                "call issue_feedback so the memory can learn from the outcome."
            )
        elif decision.status == "ambiguous":
            next_action = (
                "Compare the top one or two matches with issue_get. The memory found plausible "
                "candidates but not a single clear winner. Use issue_guardrails for proactive prevention rules, "
                "then record feedback once you confirm or reject one."
            )
        else:
            next_action = "No reliable memory hit. Use issue_guardrails for preventive hints, continue fresh debugging, then record only a verified reusable fix."
        return {
            "query_profile": profile.to_dict(),
            "decision": decision.to_dict(),
            "retrieval_event_id": event_meta.get("event_id"),
            "matches": [match.to_compact_dict() for match in matches],
            "reasoning": self._build_reasoning(event_meta.get("_ranked", [])),
            "next_action": next_action,
        }

    @staticmethod
    def _build_reasoning(ranked: list[Any]) -> dict[str, Any]:
        """Extract structured reasoning from ranked candidates for transparency (Phase 2.4)."""
        if not ranked:
            return {"top_signals": [], "session_memory": {}, "strategy_bandit": {}}

        top = ranked[0]
        features = getattr(top, "features", {}) or {}
        reasons = getattr(top, "reasons", []) or []

        # Top contributing signals: features sorted by absolute weighted contribution
        signal_entries = []
        for name, value in features.items():
            if name.startswith("_") or name in ("base_score", "strategy_bandit_final_score"):
                continue
            if abs(value) > 1e-6:
                signal_entries.append((abs(value), f"{name} ({value:+.3f})"))
        signal_entries.sort(reverse=True)
        top_signals = [entry[1] for entry in signal_entries[:7]]

        # Session memory info
        session_info: dict[str, Any] = {}
        session_penalty = features.get("session_penalty", 0.0) or features.get("session_penalty_score", 0.0)
        session_boost = features.get("session_boost", 0.0)
        if session_penalty:
            session_info["penalty"] = round(float(session_penalty), 4)
        if session_boost:
            session_info["boost"] = round(float(session_boost), 4)
        session_reasons = [r for r in reasons if "session" in r.lower() or "rejected" in r.lower()]
        if session_reasons:
            session_info["reasons"] = session_reasons

        # Strategy bandit info
        bandit_info: dict[str, Any] = {}
        bandit_adj = features.get("strategy_bandit_adjustment", 0.0)
        if abs(bandit_adj) > 1e-6:
            bandit_info["adjustment"] = round(float(bandit_adj), 4)
        if features.get("strategy_bandit_shadow_mode"):
            bandit_info["shadow_mode"] = True
            if features.get("strategy_bandit_shadow_promoted"):
                bandit_info["shadow_promoted"] = True
        neg_penalty = features.get("negative_applicability_penalty", 0.0)
        if neg_penalty:
            bandit_info["negative_applicability_penalty"] = round(float(neg_penalty), 4)
        bandit_reasons = [r for r in reasons if "bandit" in r.lower() or "strategy" in r.lower() or "negative-applicability" in r.lower()]
        if bandit_reasons:
            bandit_info["reasons"] = bandit_reasons

        return {
            "top_signals": top_signals,
            "session_memory": session_info,
            "strategy_bandit": bandit_info,
        }

    def issue_record_resolution(
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
        return self.record_service.record(
            title=title,
            raw_error=raw_error,
            canonical_fix=canonical_fix,
            prevention_rule=prevention_rule,
            project_scope=project_scope,
            user_scope=user_scope,
            canonical_symptom=canonical_symptom,
            verification_steps=verification_steps,
            tags=tags,
            error_family=error_family,
            root_cause_class=root_cause_class,
            context=context,
            file_path=file_path,
            command=command,
            domain=domain,
            stack_excerpt=stack_excerpt,
            env_json=env_json,
            repo_name=repo_name,
            git_commit=git_commit,
            session_id=session_id,
            verification_command=verification_command,
            verification_output=verification_output,
            resolution_notes=resolution_notes,
            patch_summary=patch_summary,
            patch_hash=patch_hash,
        )

    def issue_feedback(
        self,
        *,
        retrieval_event_id: int,
        feedback_type: str,
        retrieval_candidate_id: int = 0,
        candidate_rank: int = 0,
        pattern_id: int = 0,
        variant_id: int = 0,
        actor: str = "user",
        reward: float | None = None,
        notes: str = "",
    ) -> dict[str, Any]:
        return self.feedback_service.submit(
            retrieval_event_id=retrieval_event_id,
            feedback_type=feedback_type,
            retrieval_candidate_id=retrieval_candidate_id,
            candidate_rank=candidate_rank,
            pattern_id=pattern_id,
            variant_id=variant_id,
            actor=actor,
            reward=reward,
            notes=notes,
        )

    def issue_set_preference(
        self,
        *,
        instruction: str,
        project_scope: str = "global",
        user_scope: str = "",
        repo_name: str = "",
        error_family: str = "auto",
        strategy_key: str = "auto",
        command: str = "",
        file_path: str = "",
        mode: str = "prefer",
        weight: float | None = None,
        source: str = "user_prompt",
    ) -> dict[str, Any]:
        return self.preference_service.set_rule(
            instruction=instruction,
            project_scope=project_scope,
            user_scope=user_scope,
            repo_name=repo_name,
            error_family=error_family,
            strategy_key=strategy_key,
            command=command,
            file_path=file_path,
            mode=mode,
            weight=weight,
            source=source,
        )

    def issue_list_preferences(
        self,
        *,
        scope_type: str = "",
        scope_key: str = "",
        project_scope: str = "",
        repo_name: str = "",
        active_only: bool = True,
        limit: int = 20,
    ) -> dict[str, Any]:
        return self.preference_service.list_rules(
            scope_type=scope_type,
            scope_key=scope_key,
            project_scope=project_scope,
            repo_name=repo_name,
            active_only=active_only,
            limit=limit,
        )

    def issue_guardrails(
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
        return self.guardrail_service.plan(
            error_text=error_text,
            context=context,
            command=command,
            file_path=file_path,
            repo_name=repo_name,
            project_scope=project_scope,
            user_scope=user_scope,
            limit=limit,
        )

    def issue_metrics(self, *, window_days: int = 30) -> dict[str, Any]:
        return self.store.metrics_summary(window_days=window_days)

    def issue_review_queue(self, *, status: str = "pending", limit: int = 20) -> dict[str, Any]:
        rows = self.store.list_review_queue(status=status, limit=limit)
        return {
            "status": status or "all",
            "count": len(rows),
            "items": rows,
        }

    def issue_review_resolve(self, *, review_id: int, decision: str, note: str = "") -> dict[str, Any]:
        item = self.store.resolve_review_item(review_id=review_id, decision=decision, note=note)
        if item is None:
            return {"found": False, "review_id": review_id}
        return {"found": True, "item": item}

    def issue_get(self, *, pattern_id: int, include_examples: bool = True, examples_limit: int = 5) -> dict[str, Any]:
        bundle = self.store.get_pattern(
            pattern_id,
            include_examples=include_examples,
            examples_limit=examples_limit,
            include_variants=True,
            variants_limit=max(examples_limit, 10),
            include_episodes=True,
            episodes_limit=max(examples_limit, 10),
        )
        if bundle is None:
            return {"found": False, "pattern_id": pattern_id}
        return {
            "found": True,
            "pattern": bundle.pattern,
            "examples": bundle.examples,
            "variants": bundle.variants,
            "episodes": bundle.episodes,
        }

    def issue_recent(self, *, limit: int = 5, project_scope: str = "") -> dict[str, Any]:
        rows = self.store.recent_patterns(limit=limit, project_scope=project_scope)
        compact = [
            {
                "pattern_id": int(row["id"]),
                "title": row["title"],
                "project_scope": row["project_scope"],
                "error_family": row["error_family"],
                "root_cause_class": row["root_cause_class"],
                "times_seen": int(row["times_seen"]),
                "updated_at": row["updated_at"],
            }
            for row in rows
        ]
        return {"patterns": compact}

    def issue_search(self, *, query: str, project_scope: str = "", limit: int = 5, session_id: str = "") -> dict[str, Any]:
        rows, event_meta = self.matcher.search(query=query, project_scope=project_scope, limit=limit, session_id=session_id, log_event=True)
        compact = [
            {
                "pattern_id": row.pattern_id,
                "variant_id": row.variant_id,
                "retrieval_candidate_id": row.retrieval_candidate_id,
                "candidate_rank": row.candidate_rank,
                "score": round(row.score, 3),
                "title": row.title,
                "project_scope": row.project_scope,
                "domain": row.domain,
                "error_family": row.error_family,
                "root_cause_class": row.root_cause_class,
                "canonical_fix": row.canonical_fix,
            }
            for row in rows
        ]
        return {"retrieval_event_id": event_meta.get("event_id"), "patterns": compact}
