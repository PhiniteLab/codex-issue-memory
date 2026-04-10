from __future__ import annotations

from hashlib import blake2b
import logging
import math
import struct
from typing import Any

from ..models import QueryProfile
from ..normalization import tokenize
from ..storage import IssueMemoryStore

_logger = logging.getLogger(__name__)


class DenseEmbeddingIndex:
    """Local dense retrieval over hashed n-gram embeddings stored in SQLite."""

    def __init__(self, store: IssueMemoryStore) -> None:
        self.store = store
        self.model_name = store.settings.dense_model_name
        self.dim = max(int(store.settings.dense_embedding_dim), 32)
        self.similarity_floor = float(store.settings.dense_similarity_floor)

    @staticmethod
    def _hash_bytes(value: str) -> bytes:
        return blake2b(value.encode("utf-8", errors="ignore"), digest_size=8).digest()

    def _hash_index(self, value: str) -> tuple[int, float]:
        digest = self._hash_bytes(value)
        index = int.from_bytes(digest[:4], "little") % self.dim
        sign = 1.0 if (digest[4] & 1) == 0 else -1.0
        return index, sign

    def _add(self, vector: list[float], key: str, weight: float) -> None:
        index, sign = self._hash_index(key)
        vector[index] += sign * weight

    def embed_text(self, text: str) -> list[float]:
        vector = [0.0] * self.dim
        tokens = tokenize(text, max_tokens=256)
        if not tokens:
            return vector
        for token in tokens:
            self._add(vector, f"tok:{token}", 1.0)
            if len(token) >= 4:
                for start in range(0, len(token) - 2):
                    self._add(vector, f"tri:{token[start:start + 3]}", 0.35)
        for left, right in zip(tokens, tokens[1:]):
            self._add(vector, f"bi:{left}_{right}", 0.50)
        # Synonym vector augmentation: add cross-pair hashes
        from ..normalization.synonyms import synonym_pairs_for

        for token in tokens:
            for tok, syn in synonym_pairs_for(token):
                self._add(vector, f"syn:{tok}_{syn}", 0.30)
        norm = math.sqrt(sum(value * value for value in vector))
        if norm <= 1e-9:
            return vector
        return [value / norm for value in vector]

    @staticmethod
    def pack_vector(vector: list[float]) -> bytes:
        return struct.pack(f"<{len(vector)}f", *vector)

    @staticmethod
    def unpack_vector(blob: bytes, *, expected_dim: int) -> list[float]:
        if not blob:
            return [0.0] * expected_dim
        try:
            values = list(struct.unpack(f"<{expected_dim}f", blob))
        except struct.error:
            _logger.warning(
                "Corrupt embedding blob: expected %d floats (%d bytes), got %d bytes",
                expected_dim, expected_dim * 4, len(blob),
            )
            return [0.0] * expected_dim
        return [float(value) for value in values]

    @staticmethod
    def _cosine(left: list[float], right: list[float]) -> float:
        if not left or not right or len(left) != len(right):
            return 0.0
        return sum(left_value * right_value for left_value, right_value in zip(left, right))

    @staticmethod
    def _compose_pattern_text(row: dict[str, Any]) -> str:
        return " ".join(
            [
                str(row.get("title", "")),
                str(row.get("canonical_symptom", "")),
                str(row.get("canonical_fix", "")),
                str(row.get("prevention_rule", "")),
                str(row.get("verification_steps", "")),
                str(row.get("tags", "")),
                str(row.get("error_family", "")),
                str(row.get("root_cause_class", "")),
                str(row.get("domain", "")),
            ]
        )

    @staticmethod
    def _compose_variant_text(row: dict[str, Any]) -> str:
        raw_tags = row.get("variant_tags_json")
        variant_tags = " ".join(str(tag) for tag in raw_tags) if isinstance(raw_tags, list) else str(raw_tags or "")
        return " ".join(
            [
                str(row.get("pattern_title", "")),
                str(row.get("title", "")),
                str(row.get("canonical_symptom", "")),
                str(row.get("pattern_canonical_fix", "")),
                str(row.get("canonical_fix", "")),
                str(row.get("prevention_rule", "")),
                str(row.get("verification_steps", "")),
                str(row.get("patch_summary", "")),
                str(row.get("pattern_tags", "")),
                variant_tags,
                str(row.get("error_family", "")),
                str(row.get("root_cause_class", "")),
                str(row.get("domain", "")),
            ]
        )

    @staticmethod
    def _query_text(profile: QueryProfile) -> str:
        return " ".join(
            [
                profile.normalized_text,
                " ".join(profile.symptom_tokens),
                " ".join(profile.context_tokens),
                " ".join(profile.command_tokens),
                " ".join(profile.path_tokens),
                " ".join(profile.exception_types),
                profile.error_family,
                profile.root_cause_class,
            ]
        )

    def refresh_pattern(self, pattern_id: int) -> None:
        row = self.store.get_pattern_embedding_source(pattern_id)
        if row is None:
            return
        vector = self.embed_text(self._compose_pattern_text(row))
        self.store.upsert_embedding(
            object_type="pattern",
            object_id=pattern_id,
            embedding_model=self.model_name,
            vector_dim=self.dim,
            vector_blob=self.pack_vector(vector),
            norm=1.0,
        )

    def refresh_variant(self, variant_id: int) -> None:
        row = self.store.get_variant_embedding_source(variant_id)
        if row is None:
            return
        vector = self.embed_text(self._compose_variant_text(row))
        self.store.upsert_embedding(
            object_type="variant",
            object_id=variant_id,
            embedding_model=self.model_name,
            vector_dim=self.dim,
            vector_blob=self.pack_vector(vector),
            norm=1.0,
        )

    def _ensure_variant_embeddings(self, rows: list[dict[str, Any]]) -> dict[int, list[float]]:
        if not rows:
            return {}
        variant_ids = [int(row["variant_id"]) for row in rows]
        stored = self.store.load_embeddings(
            object_type="variant",
            object_ids=variant_ids,
            embedding_model=self.model_name,
        )
        vectors: dict[int, list[float]] = {}
        for row in rows:
            variant_id = int(row["variant_id"])
            existing = stored.get(variant_id)
            if existing is None:
                vector = self.embed_text(self._compose_variant_text(row))
                self.store.upsert_embedding(
                    object_type="variant",
                    object_id=variant_id,
                    embedding_model=self.model_name,
                    vector_dim=self.dim,
                    vector_blob=self.pack_vector(vector),
                    norm=1.0,
                )
                vectors[variant_id] = vector
            else:
                vectors[variant_id] = self.unpack_vector(existing["vector_blob"], expected_dim=self.dim)
        return vectors

    def _ensure_pattern_embeddings(self, rows: list[dict[str, Any]]) -> dict[int, list[float]]:
        if not rows:
            return {}
        pattern_ids = [int(row["id"]) for row in rows]
        stored = self.store.load_embeddings(
            object_type="pattern",
            object_ids=pattern_ids,
            embedding_model=self.model_name,
        )
        vectors: dict[int, list[float]] = {}
        for row in rows:
            pattern_id = int(row["id"])
            existing = stored.get(pattern_id)
            if existing is None:
                vector = self.embed_text(self._compose_pattern_text(row))
                self.store.upsert_embedding(
                    object_type="pattern",
                    object_id=pattern_id,
                    embedding_model=self.model_name,
                    vector_dim=self.dim,
                    vector_blob=self.pack_vector(vector),
                    norm=1.0,
                )
                vectors[pattern_id] = vector
            else:
                vectors[pattern_id] = self.unpack_vector(existing["vector_blob"], expected_dim=self.dim)
        return vectors

    def query_variants(self, profile: QueryProfile, *, project_scope: str, limit: int) -> list[dict[str, Any]]:
        rows = self.store.get_dense_variant_sources(
            project_scope=project_scope,
            error_family=profile.error_family,
            root_cause_class=profile.root_cause_class,
            limit=max(limit * 20, 160),
        )
        if not rows:
            return []
        vectors = self._ensure_variant_embeddings(rows)
        query_vector = self.embed_text(self._query_text(profile))
        scored: list[tuple[float, dict[str, Any]]] = []
        for row in rows:
            variant_id = int(row["variant_id"])
            similarity = self._cosine(query_vector, vectors.get(variant_id, []))
            if similarity < self.similarity_floor:
                continue
            raw_tags = row.get("variant_tags_json")
            variant_tags = raw_tags if isinstance(raw_tags, list) else []
            candidate = {
                "id": int(row["pattern_id"]),
                "pattern_id": int(row["pattern_id"]),
                "variant_id": variant_id,
                "candidate_type": "variant",
                "title": str(row.get("pattern_title", "")),
                "project_scope": str(row.get("project_scope", "global")),
                "domain": str(row.get("domain", "generic")),
                "error_family": str(row.get("error_family", "generic_runtime_error")),
                "root_cause_class": str(row.get("root_cause_class", "unknown")),
                "canonical_symptom": str(row.get("canonical_symptom", "")),
                "canonical_fix": str(row.get("pattern_canonical_fix", "")),
                "prevention_rule": str(row.get("prevention_rule", "")),
                "verification_steps": str(row.get("pattern_verification_steps", "")),
                "tags": str(row.get("pattern_tags", "")),
                "signature": str(row.get("signature", "")),
                "times_seen": int(row.get("times_seen", 0)),
                "confidence": float(row.get("pattern_confidence", 0.5)),
                "created_at": str(row.get("pattern_created_at", "")),
                "updated_at": str(row.get("pattern_updated_at", "")),
                "best_variant": {
                    "id": variant_id,
                    "pattern_id": int(row["pattern_id"]),
                    "variant_key": str(row.get("variant_key", "")),
                    "title": str(row.get("title", "")),
                    "canonical_fix": str(row.get("canonical_fix", "")),
                    "verification_steps": str(row.get("verification_steps", "")),
                    "rollback_steps": str(row.get("rollback_steps", "")),
                    "tags_json": list(variant_tags),
                    "patch_summary": str(row.get("patch_summary", "")),
                    "confidence": float(row.get("confidence", 0.5)),
                    "memory_strength": float(row.get("memory_strength", 0.5)),
                    "times_used": int(row.get("times_used", 0)),
                    "success_count": int(row.get("success_count", 0)),
                    "reject_count": int(row.get("reject_count", 0)),
                    "repo_fingerprint": str(row.get("repo_fingerprint", "")),
                    "env_fingerprint": str(row.get("env_fingerprint", "")),
                    "command_signature": str(row.get("command_signature", "")),
                    "file_path_signature": str(row.get("file_path_signature", "")),
                    "stack_signature": str(row.get("stack_signature", "")),
                    "updated_at": str(row.get("variant_updated_at", "")),
                },
                "variant_match_score": 0.0,
                "examples": [],
                "episodes": [],
                "retrieval_signals": {},
                "dense_score": round(min(max(similarity, 0.0), 1.0), 6),
            }
            scored.append((similarity, candidate))
        scored.sort(key=lambda item: (-item[0], int(item[1]["pattern_id"]), int(item[1]["variant_id"])))
        results: list[dict[str, Any]] = []
        for dense_rank, (_score, candidate) in enumerate(scored[:limit], start=1):
            candidate["retrieval_signals"] = {"dense_rank": dense_rank, "dense_score": candidate["dense_score"]}
            results.append(candidate)
        return results

    def query_patterns(self, profile: QueryProfile, *, project_scope: str, limit: int) -> list[dict[str, Any]]:
        rows = self.store.get_dense_pattern_sources(
            project_scope=project_scope,
            error_family=profile.error_family,
            root_cause_class=profile.root_cause_class,
            limit=max(limit * 16, 120),
        )
        if not rows:
            return []
        vectors = self._ensure_pattern_embeddings(rows)
        query_vector = self.embed_text(self._query_text(profile))
        scored: list[tuple[float, dict[str, Any]]] = []
        for row in rows:
            pattern_id = int(row["id"])
            similarity = self._cosine(query_vector, vectors.get(pattern_id, []))
            if similarity < self.similarity_floor:
                continue
            candidate = {
                "id": pattern_id,
                "pattern_id": pattern_id,
                "variant_id": None,
                "candidate_type": "pattern",
                "title": str(row.get("title", "")),
                "project_scope": str(row.get("project_scope", "global")),
                "domain": str(row.get("domain", "generic")),
                "error_family": str(row.get("error_family", "generic_runtime_error")),
                "root_cause_class": str(row.get("root_cause_class", "unknown")),
                "canonical_symptom": str(row.get("canonical_symptom", "")),
                "canonical_fix": str(row.get("canonical_fix", "")),
                "prevention_rule": str(row.get("prevention_rule", "")),
                "verification_steps": str(row.get("verification_steps", "")),
                "tags": str(row.get("tags", "")),
                "signature": str(row.get("signature", "")),
                "times_seen": int(row.get("times_seen", 0)),
                "confidence": float(row.get("confidence", 0.5)),
                "created_at": str(row.get("created_at", "")),
                "updated_at": str(row.get("updated_at", "")),
                "best_variant": None,
                "variant_match_score": 0.0,
                "examples": [],
                "episodes": [],
                "retrieval_signals": {},
                "dense_score": round(min(max(similarity, 0.0), 1.0), 6),
            }
            scored.append((similarity, candidate))
        scored.sort(key=lambda item: (-item[0], int(item[1]["pattern_id"])))
        results: list[dict[str, Any]] = []
        for dense_rank, (_score, candidate) in enumerate(scored[:limit], start=1):
            candidate["retrieval_signals"] = {"dense_rank": dense_rank, "dense_score": candidate["dense_score"]}
            results.append(candidate)
        return results
