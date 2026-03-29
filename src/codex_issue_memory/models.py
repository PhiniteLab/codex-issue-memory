from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(slots=True)
class QueryProfile:
    """Normalized representation of an incoming failure query.

    The v2 profile keeps the original coarse fields used by the current matcher,
    while also carrying finer-grained token groups and lightweight fingerprints
    needed by later patches.
    """

    raw_text: str
    normalized_text: str
    tokens: list[str]
    exception_types: list[str]
    error_family: str
    root_cause_class: str
    tags: list[str]
    evidence: list[str]
    symptom_tokens: list[str] = field(default_factory=list)
    context_tokens: list[str] = field(default_factory=list)
    command_tokens: list[str] = field(default_factory=list)
    path_tokens: list[str] = field(default_factory=list)
    stack_signature: str = ""
    env_fingerprint: str = ""
    repo_fingerprint: str = ""
    repo_name: str = ""
    project_scope: str = "global"
    user_scope: str = ""
    entity_slots: dict[str, Any] = field(default_factory=dict)
    strategy_hints: list[str] = field(default_factory=list)
    command_signature: str = ""
    path_signature: str = ""
    pattern_key: str = ""
    variant_key: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class MatchResult:
    """Result of matching a query to a stored pattern."""

    pattern_id: int
    score: float
    title: str
    project_scope: str
    domain: str
    error_family: str
    root_cause_class: str
    canonical_fix: str
    prevention_rule: str
    verification_steps: str
    times_seen: int
    why: list[str]
    variant_id: int | None = None
    candidate_rank: int | None = None
    retrieval_candidate_id: int | None = None

    def to_compact_dict(self) -> dict[str, Any]:
        payload = {
            "pattern_id": self.pattern_id,
            "score": round(self.score, 3),
            "title": self.title,
            "project_scope": self.project_scope,
            "domain": self.domain,
            "error_family": self.error_family,
            "root_cause_class": self.root_cause_class,
            "canonical_fix": self.canonical_fix,
            "prevention_rule": self.prevention_rule,
            "verification_steps": self.verification_steps,
            "times_seen": self.times_seen,
            "why": self.why,
        }
        if self.variant_id is not None:
            payload["variant_id"] = self.variant_id
        if self.candidate_rank is not None:
            payload["candidate_rank"] = self.candidate_rank
        if self.retrieval_candidate_id is not None:
            payload["retrieval_candidate_id"] = self.retrieval_candidate_id
        return payload


@dataclass(slots=True)
class MatchDecision:
    """Classifier over ranked retrieval results."""

    status: str
    confidence: float
    reason: str
    top_score: float = 0.0
    second_score: float = 0.0
    gap: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "confidence": round(self.confidence, 3),
            "reason": self.reason,
            "top_score": round(self.top_score, 3),
            "second_score": round(self.second_score, 3),
            "gap": round(self.gap, 3),
        }
