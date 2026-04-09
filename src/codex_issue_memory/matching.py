from __future__ import annotations

import time
from typing import Any

from .models import MatchDecision, MatchResult, QueryProfile
from .normalization import build_query_profile
from .retrieval import CandidateRetriever, HeuristicRanker, MatchDecisionPolicy, RankedCandidate
from .settings import Settings
from .storage import IssueMemoryStore


class IssueMatcher:
    """Variant-first hybrid retrieval + deterministic ranking layer for issue matching."""

    def __init__(self, store: IssueMemoryStore, settings: Settings | None = None) -> None:
        self.store = store
        self.settings = settings or store.settings
        self.retriever = CandidateRetriever(store)
        self.ranker = HeuristicRanker(store, self.settings)
        self.calibration_profile = self.store.load_calibration_profile() if self.settings.enable_calibration_profile else {}

    def _threshold_bundle(self, error_family: str) -> dict[str, float]:
        bundle = {
            "accept_threshold": float(self.settings.match_accept_threshold),
            "weak_threshold": float(self.settings.match_weak_threshold),
            "ambiguity_margin": float(self.settings.ambiguity_margin),
        }
        profile = self.calibration_profile if isinstance(self.calibration_profile, dict) else {}
        raw_global_overrides = profile.get("global")
        global_overrides = raw_global_overrides if isinstance(raw_global_overrides, dict) else {}
        for key in bundle:
            if key in global_overrides:
                bundle[key] = float(global_overrides[key])
        raw_family_overrides = profile.get("families")
        family_overrides = raw_family_overrides if isinstance(raw_family_overrides, dict) else {}
        raw_selected_family = family_overrides.get(error_family)
        selected_family = raw_selected_family if isinstance(raw_selected_family, dict) else {}
        for key in bundle:
            if key in selected_family:
                bundle[key] = float(selected_family[key])
        return bundle

    def _decision_policy_for_family(self, error_family: str) -> MatchDecisionPolicy:
        bundle = self._threshold_bundle(error_family)
        return MatchDecisionPolicy(
            accept_threshold=bundle["accept_threshold"],
            weak_threshold=bundle["weak_threshold"],
            ambiguity_margin=bundle["ambiguity_margin"],
        )

    def ranked_candidates(
        self,
        profile: QueryProfile,
        *,
        project_scope: str,
        limit: int,
        session_id: str = "",
        repo_name: str = "",
        retrieval_context: str = "match",
    ) -> list[RankedCandidate]:
        raw_candidates = self.retriever.retrieve(
            profile,
            project_scope=project_scope,
            limit=max(limit * 8, 24),
            session_id=session_id,
            repo_name=repo_name or getattr(profile, "repo_name", ""),
        )
        return self.ranker.rank(
            profile,
            raw_candidates,
            project_scope=project_scope,
            limit=max(limit * 3, 8),
            use_strategy_overlay=retrieval_context == "match",
        )

    def match_with_decision(
        self,
        profile: QueryProfile,
        *,
        project_scope: str = "global",
        limit: int = 3,
        session_id: str = "",
        repo_name: str = "",
        retrieval_mode: str = "match",
        log_event: bool = True,
    ) -> tuple[list[MatchResult], MatchDecision, dict[str, Any]]:
        start = time.perf_counter()
        ranked = self.ranked_candidates(
            profile,
            project_scope=project_scope,
            limit=limit,
            session_id=session_id,
            repo_name=repo_name,
            retrieval_context=retrieval_mode,
        )
        decision = self._decision_policy_for_family(profile.error_family).decide(ranked)
        visible_ranked = [] if decision.status == "abstain" else ranked[:limit]
        visible_matches = [self._to_match_result(item) for item in visible_ranked]
        latency_ms = int((time.perf_counter() - start) * 1000)

        event_meta: dict[str, Any] = {}
        if log_event and self.settings.telemetry_enabled:
            event_meta = self.store.log_retrieval_event(
                profile=profile,
                ranked=ranked,
                decision=decision,
                project_scope=project_scope,
                session_id=session_id,
                repo_name=repo_name,
                retrieval_mode=retrieval_mode,
                latency_ms=latency_ms,
            )
            ids_by_rank = {
                int(rank): int(candidate_id)
                for rank, candidate_id in event_meta.get("candidate_ids_by_rank", {}).items()
            }
            for index, match in enumerate(visible_matches, start=1):
                match.candidate_rank = index
                match.retrieval_candidate_id = ids_by_rank.get(index)

        return visible_matches, decision, event_meta

    def match(
        self,
        profile: QueryProfile,
        *,
        project_scope: str = "global",
        limit: int = 3,
        session_id: str = "",
    ) -> list[MatchResult]:
        matches, _decision, _event_meta = self.match_with_decision(
            profile,
            project_scope=project_scope,
            limit=limit,
            session_id=session_id,
            repo_name=getattr(profile, "repo_name", ""),
            log_event=False,
        )
        return matches

    def search(
        self,
        *,
        query: str,
        project_scope: str = "",
        user_scope: str = "",
        limit: int = 5,
        session_id: str = "",
        log_event: bool = True,
    ) -> tuple[list[MatchResult], dict[str, Any]]:
        profile = build_query_profile(error_text=query, project_scope=project_scope or "global", user_scope=user_scope)
        ranked = self.ranked_candidates(
            profile,
            project_scope=project_scope,
            limit=limit,
            session_id=session_id,
            repo_name="",
            retrieval_context="search",
        )
        decision = self._decision_policy_for_family(profile.error_family).decide(ranked)
        visible_ranked = ranked[:limit]
        matches = [self._to_match_result(item) for item in visible_ranked]
        event_meta: dict[str, Any] = {}
        if log_event and self.settings.telemetry_enabled:
            event_meta = self.store.log_retrieval_event(
                profile=profile,
                ranked=ranked,
                decision=decision,
                project_scope=project_scope,
                session_id=session_id,
                repo_name="",
                retrieval_mode="search",
                latency_ms=0,
            )
            ids_by_rank = {
                int(rank): int(candidate_id)
                for rank, candidate_id in event_meta.get("candidate_ids_by_rank", {}).items()
            }
            for index, match in enumerate(matches, start=1):
                match.candidate_rank = index
                match.retrieval_candidate_id = ids_by_rank.get(index)
        return matches, event_meta

    def _to_match_result(self, ranked: RankedCandidate) -> MatchResult:
        candidate = ranked.candidate
        best_variant = candidate.get("best_variant") or {}
        pattern_id = int(candidate.get("pattern_id", candidate["id"]))
        raw_variant_id = candidate.get("variant_id")
        variant_id = (
            int(raw_variant_id)
            if raw_variant_id not in (None, "")
            else (int(best_variant["id"]) if best_variant else None)
        )
        canonical_fix = str(best_variant.get("canonical_fix") or candidate["canonical_fix"])
        verification_steps = str(best_variant.get("verification_steps") or candidate["verification_steps"])
        why = list(ranked.reasons)
        if variant_id is not None and "variant-first-candidate" not in why:
            why = ["variant-first-candidate", *why]
        return MatchResult(
            pattern_id=pattern_id,
            score=ranked.score,
            title=str(best_variant.get("title") or candidate["title"]),
            project_scope=str(candidate["project_scope"]),
            domain=str(candidate["domain"]),
            error_family=str(candidate["error_family"]),
            root_cause_class=str(candidate["root_cause_class"]),
            canonical_fix=self._truncate(canonical_fix, 220),
            prevention_rule=self._truncate(str(candidate["prevention_rule"]), 180),
            verification_steps=self._truncate(verification_steps, 180),
            times_seen=int(candidate["times_seen"]),
            why=why,
            variant_id=variant_id,
        )

    @staticmethod
    def _truncate(text: str, limit: int) -> str:
        text = text.strip()
        if len(text) <= limit:
            return text
        return text[: limit - 1].rstrip() + "…"
