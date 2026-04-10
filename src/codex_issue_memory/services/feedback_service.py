from __future__ import annotations

from typing import Any

from ..storage import IssueMemoryStore, WEAK_FEEDBACK_WEIGHTS
from .session_service import SessionService


DEFAULT_REWARDS: dict[str, float] = {
    "candidate_accepted": 0.35,
    "candidate_rejected": -0.60,
    "fix_verified": 1.00,
    "false_positive": -2.50,
    "merge_confirmed": 0.40,
    "merge_rejected": -0.40,
    "split_confirmed": 0.40,
    "split_rejected": -0.40,
}

POSITIVE_FEEDBACK = {"candidate_accepted", "fix_verified", "merge_confirmed", "split_confirmed"}
NEGATIVE_FEEDBACK = {"candidate_rejected", "false_positive", "merge_rejected", "split_rejected"}
GLOBAL_LEARNING_FEEDBACK = {"fix_verified", "false_positive"}
LEARNABLE_FEEDBACK = GLOBAL_LEARNING_FEEDBACK | set(WEAK_FEEDBACK_WEIGHTS)


class FeedbackService:
    """Turn retrieval feedback into telemetry, short-term memory and strategy updates."""

    def __init__(self, store: IssueMemoryStore, session_service: SessionService) -> None:
        self.store = store
        self.session_service = session_service

    def submit(
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
        event = self.store.get_retrieval_event(retrieval_event_id)
        if event is None:
            raise KeyError(f"Retrieval event {retrieval_event_id} not found")

        candidate = self.store.resolve_retrieval_candidate(
            retrieval_event_id=retrieval_event_id,
            retrieval_candidate_id=retrieval_candidate_id or None,
            candidate_rank=candidate_rank or None,
            pattern_id=pattern_id or None,
            variant_id=variant_id or None,
        )
        if candidate is None:
            raise KeyError(
                "No retrieval candidate could be resolved from the supplied identifiers. "
                "Pass retrieval_candidate_id, candidate_rank, pattern_id or variant_id."
            )

        applied_reward = DEFAULT_REWARDS.get(feedback_type, 0.0) if reward is None else float(reward)
        feedback_result = self.store.submit_feedback(
            retrieval_event_id=retrieval_event_id,
            retrieval_candidate_id=int(candidate["id"]),
            pattern_id=int(candidate["pattern_id"]) if candidate.get("pattern_id") is not None else None,
            variant_id=int(candidate["variant_id"]) if candidate.get("variant_id") is not None else None,
            episode_id=None,
            feedback_type=feedback_type,
            reward=applied_reward,
            actor=actor,
            notes=notes,
        )
        feedback_row = feedback_result["feedback_row"]

        session_memory = None
        if feedback_type in NEGATIVE_FEEDBACK:
            session_memory = self.session_service.remember_rejection(
                session_id=str(event.get("session_id", "")),
                project_scope=str(event.get("project_scope", "global")),
                repo_name=str(event.get("repo_name", "")),
                pattern_id=int(candidate["pattern_id"]),
                variant_id=int(candidate["variant_id"]) if candidate.get("variant_id") is not None else None,
                feedback_type=feedback_type,
                notes=notes,
            )
        elif feedback_type in POSITIVE_FEEDBACK:
            session_memory = self.session_service.remember_acceptance(
                session_id=str(event.get("session_id", "")),
                project_scope=str(event.get("project_scope", "global")),
                repo_name=str(event.get("repo_name", "")),
                pattern_id=int(candidate["pattern_id"]),
                variant_id=int(candidate["variant_id"]) if candidate.get("variant_id") is not None else None,
                feedback_type=feedback_type,
                notes=notes,
            )

        learning = None

        bandit_update = None
        if self.store.settings.enable_strategy_bandit and feedback_type in LEARNABLE_FEEDBACK:
            bandit_update = {
                "policy": "conservative_hierarchical_thompson",
                "global_update_applied": bool(feedback_result.get("global_update_applied", False)),
                "strategy_stat_updates": feedback_result.get("strategy_stat_updates", []),
                "variant_stat_update": feedback_result.get("variant_stat_update"),
            }

        feature_log_count = 0
        candidate_features = candidate.get("feature_json")
        if isinstance(candidate_features, dict) and candidate_features:
            feature_log_count = self.store.log_feature_outcomes(
                feedback_event_id=int(feedback_row["id"]),
                retrieval_candidate_id=int(candidate["id"]),
                features=candidate_features,
                feedback_type=feedback_type,
                reward=applied_reward,
                error_family=str(event.get("error_family", "")),
                project_scope=str(event.get("project_scope", "global")),
            )

        return {
            "status": "ok",
            "retrieval_event_id": retrieval_event_id,
            "retrieval_candidate_id": int(candidate["id"]),
            "feedback_event_id": int(feedback_row["id"]),
            "feedback_type": feedback_type,
            "reward": applied_reward,
            "resolved_candidate": {
                "candidate_rank": int(candidate["candidate_rank"]),
                "pattern_id": int(candidate["pattern_id"]) if candidate.get("pattern_id") is not None else None,
                "variant_id": int(candidate["variant_id"]) if candidate.get("variant_id") is not None else None,
            },
            "pattern_update": feedback_result.get("pattern_update"),
            "variant_update": feedback_result.get("variant_update"),
            "strategy_stat_updates": feedback_result.get("strategy_stat_updates", []),
            "variant_stat_update": feedback_result.get("variant_stat_update"),
            "global_update_applied": bool(feedback_result.get("global_update_applied", False)),
            "negative_applicability_applied": bool(feedback_result.get("negative_applicability_applied", False)),
            "session_memory": session_memory,
            "learning": learning,
            "bandit": bandit_update,
            "feature_log_count": feature_log_count,
        }
