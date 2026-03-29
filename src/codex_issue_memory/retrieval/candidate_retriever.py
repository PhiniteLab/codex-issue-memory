from __future__ import annotations

import re
from typing import Any

from ..models import QueryProfile
from ..storage import IssueMemoryStore
from .dense_index import DenseEmbeddingIndex


class CandidateRetriever:
    """Collect variant-first hybrid candidates from lexical and dense retrieval."""

    def __init__(self, store: IssueMemoryStore) -> None:
        self.store = store
        self.dense_index = DenseEmbeddingIndex(store) if store.settings.enable_dense_retrieval else None

    @staticmethod
    def _safe_int(value: Any, *, default: int = 0) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _safe_float(value: Any, *, default: float = 0.0) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def make_fts_query(profile: QueryProfile) -> str:
        parts = (
            profile.exception_types
            + profile.symptom_tokens[:8]
            + profile.context_tokens[:8]
            + profile.command_tokens[:4]
            + profile.path_tokens[:4]
            + profile.tokens[:8]
        )
        clean: list[str] = []
        seen: set[str] = set()
        for part in parts:
            token = re.sub(r"[^a-z0-9_]+", "_", str(part).strip().lower()).strip("_")
            if len(token) < 2 or token in seen:
                continue
            clean.append(token)
            seen.add(token)
            if len(clean) >= 14:
                break
        return " OR ".join(clean)

    @classmethod
    def _normalize_pattern_candidate(cls, candidate: dict[str, object]) -> dict[str, object]:
        variant = candidate.get("best_variant")
        variant_id = None
        if isinstance(variant, dict) and variant.get("id") is not None:
            variant_id = cls._safe_int(variant.get("id"), default=0)
        pattern_id = cls._safe_int(candidate.get("id"), default=0)
        normalized = dict(candidate)
        normalized.setdefault("pattern_id", pattern_id)
        normalized["variant_id"] = variant_id if variant_id not in (0, None) else None
        normalized.setdefault("candidate_type", "variant" if variant_id not in (0, None) else "pattern")
        normalized.setdefault("episodes", [])
        normalized.setdefault("session_boost", 0.0)
        normalized.setdefault("session_penalty", 0.0)
        normalized.setdefault("dense_score", cls._safe_float(candidate.get("dense_score"), default=0.0))
        normalized.setdefault("variant_match_score", cls._safe_float(candidate.get("variant_match_score"), default=0.0))
        normalized.setdefault("retrieval_signals", {})
        return normalized

    @classmethod
    def _merge_candidate(cls, target: dict[str, object], incoming: dict[str, object]) -> None:
        raw_existing_signals = target.get("retrieval_signals", {})
        existing_signals = dict(raw_existing_signals) if isinstance(raw_existing_signals, dict) else {}
        incoming_signals = incoming.get("retrieval_signals", {})
        if isinstance(incoming_signals, dict):
            existing_signals.update(incoming_signals)
        target["retrieval_signals"] = existing_signals
        target["dense_score"] = max(
            cls._safe_float(target.get("dense_score"), default=0.0),
            cls._safe_float(incoming.get("dense_score"), default=0.0),
        )
        target["variant_match_score"] = max(
            cls._safe_float(target.get("variant_match_score"), default=0.0),
            cls._safe_float(incoming.get("variant_match_score"), default=0.0),
        )
        if not target.get("best_variant") and incoming.get("best_variant"):
            target["best_variant"] = incoming.get("best_variant")
            raw_variant_id = incoming.get("variant_id")
            target["variant_id"] = cls._safe_int(raw_variant_id, default=0) or None
            target["candidate_type"] = "variant"
        if not target.get("examples") and incoming.get("examples"):
            target["examples"] = incoming.get("examples")
        if not target.get("episodes") and incoming.get("episodes"):
            target["episodes"] = incoming.get("episodes")

    def _pair_key(self, candidate: dict[str, object]) -> tuple[int, int | None]:
        pattern_id = self._safe_int(candidate.get("pattern_id", candidate.get("id")), default=0)
        raw_variant_id = candidate.get("variant_id")
        variant_id = self._safe_int(raw_variant_id, default=0) or None
        return pattern_id, variant_id

    def retrieve(
        self,
        profile: QueryProfile,
        *,
        project_scope: str,
        limit: int,
        session_id: str = "",
        repo_name: str = "",
    ) -> list[dict[str, object]]:
        fts_query = self.make_fts_query(profile)
        merged: dict[tuple[int, int | None], dict[str, object]] = {}

        lexical_candidates = self.store.variant_candidates(
            fts_query=fts_query,
            project_scope=project_scope,
            error_family=profile.error_family,
            root_cause_class=profile.root_cause_class,
            repo_fingerprint=profile.repo_fingerprint,
            env_fingerprint=profile.env_fingerprint,
            command_signature=profile.command_signature,
            path_signature=profile.path_signature,
            stack_signature=profile.stack_signature,
            limit=max(limit * 2, 10),
        )
        for lexical in lexical_candidates:
            normalized = self._normalize_pattern_candidate(lexical)
            merged[self._pair_key(normalized)] = normalized

        if self.dense_index is not None:
            dense_variants = self.dense_index.query_variants(
                profile,
                project_scope=project_scope,
                limit=max(limit * 2, self.store.settings.dense_candidate_limit),
            )
            for dense_candidate in dense_variants:
                normalized = self._normalize_pattern_candidate(dense_candidate)
                pair = self._pair_key(normalized)
                existing = merged.get(pair)
                if existing is None:
                    merged[pair] = normalized
                else:
                    self._merge_candidate(existing, normalized)

        fallback_patterns = self.store.pattern_candidates(
            fts_query=fts_query,
            project_scope=project_scope,
            error_family=profile.error_family,
            root_cause_class=profile.root_cause_class,
            limit=max(limit // 2, 3),
        )
        if self.dense_index is not None:
            dense_patterns = self.dense_index.query_patterns(
                profile,
                project_scope=project_scope,
                limit=max(limit, 4),
            )
            fallback_patterns.extend(dense_patterns)

        if fallback_patterns:
            self.store.enrich_candidates_with_variants(
                fallback_patterns,
                repo_fingerprint=profile.repo_fingerprint,
                env_fingerprint=profile.env_fingerprint,
                command_signature=profile.command_signature,
                path_signature=profile.path_signature,
                stack_signature=profile.stack_signature,
            )
            for fallback in fallback_patterns:
                normalized = self._normalize_pattern_candidate(fallback)
                pair = self._pair_key(normalized)
                existing = merged.get(pair)
                if existing is None:
                    merged[pair] = normalized
                else:
                    self._merge_candidate(existing, normalized)

        candidates = list(merged.values())
        if session_id:
            self.store.apply_session_penalties(
                candidates,
                session_id=session_id,
                project_scope=project_scope or "global",
                repo_name=repo_name or getattr(profile, "repo_name", ""),
            )
        else:
            for candidate in candidates:
                candidate.setdefault("session_penalty", 0.0)
                candidate.setdefault("session_boost", 0.0)
        return candidates
