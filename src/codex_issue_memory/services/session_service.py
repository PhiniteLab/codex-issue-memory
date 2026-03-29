from __future__ import annotations

from typing import Any

from ..storage import IssueMemoryStore


class SessionService:
    """Short-term working memory for accepted/rejected retrieval choices."""

    def __init__(self, store: IssueMemoryStore) -> None:
        self.store = store

    @staticmethod
    def _variant_key(prefix: str, *, pattern_id: int, variant_id: int | None) -> str:
        if variant_id is None:
            return f"{prefix}_pattern:{pattern_id}"
        return f"{prefix}_variant:{pattern_id}:{variant_id}"

    def remember_rejection(
        self,
        *,
        session_id: str,
        project_scope: str,
        repo_name: str,
        pattern_id: int,
        variant_id: int | None,
        feedback_type: str,
        notes: str = "",
    ) -> dict[str, Any] | None:
        if not session_id:
            return None
        memory_key = self._variant_key("rejected", pattern_id=pattern_id, variant_id=variant_id)
        value = {
            "pattern_id": pattern_id,
            "variant_id": variant_id,
            "feedback_type": feedback_type,
            "notes": notes,
        }
        self.store.clear_session_memory_key(
            session_id=session_id,
            memory_key=self._variant_key("accepted", pattern_id=pattern_id, variant_id=variant_id),
        )
        if variant_id is None:
            self.store.clear_session_memory_key(
                session_id=session_id,
                memory_key=self._variant_key("accepted", pattern_id=pattern_id, variant_id=None),
            )
        return self.store.upsert_session_memory(
            session_id=session_id,
            project_scope=project_scope,
            repo_name=repo_name,
            memory_key=memory_key,
            memory_value=value,
            salience=0.9,
            ttl_seconds=self.store.settings.session_ttl_seconds,
        )

    def remember_acceptance(
        self,
        *,
        session_id: str,
        project_scope: str,
        repo_name: str,
        pattern_id: int,
        variant_id: int | None,
        feedback_type: str,
        notes: str = "",
    ) -> dict[str, Any] | None:
        if not session_id:
            return None
        memory_key = self._variant_key("accepted", pattern_id=pattern_id, variant_id=variant_id)
        value = {
            "pattern_id": pattern_id,
            "variant_id": variant_id,
            "feedback_type": feedback_type,
            "notes": notes,
        }
        self.store.clear_session_memory_key(
            session_id=session_id,
            memory_key=self._variant_key("rejected", pattern_id=pattern_id, variant_id=variant_id),
        )
        if variant_id is not None:
            self.store.clear_session_memory_key(
                session_id=session_id,
                memory_key=self._variant_key("rejected", pattern_id=pattern_id, variant_id=None),
            )
        return self.store.upsert_session_memory(
            session_id=session_id,
            project_scope=project_scope,
            repo_name=repo_name,
            memory_key=memory_key,
            memory_value=value,
            salience=0.55,
            ttl_seconds=self.store.settings.session_ttl_seconds,
        )

    def snapshot(self, session_id: str) -> list[dict[str, Any]]:
        return self.store.get_session_memory(session_id)
