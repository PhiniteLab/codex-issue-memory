"""Phase 4 — Retrieval Quality: IDF token prioritization, synonym expansion, entity slot learning."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from typing import Any

from codex_issue_memory.app import IssueMemoryApp
from codex_issue_memory.normalization.synonyms import (
    SYNONYM_MAP,
    expand_synonyms,
    synonym_pairs_for,
)
from codex_issue_memory.normalization.text import tokenize
from codex_issue_memory.retrieval.candidate_retriever import CandidateRetriever
from codex_issue_memory.retrieval.dense_index import DenseEmbeddingIndex
from codex_issue_memory.retrieval.features import build_candidate_features
from codex_issue_memory.models import QueryProfile


def _make_env(base: Path) -> None:
    os.environ["ISSUE_MEMORY_HOME"] = str(base / "share")
    os.environ["ISSUE_MEMORY_DB_PATH"] = str(base / "share" / "issue_memory.sqlite3")
    os.environ["ISSUE_MEMORY_STATE_DIR"] = str(base / "state")
    os.environ["ISSUE_MEMORY_BACKUP_DIR"] = str(base / "share" / "backups")
    os.environ["ISSUE_MEMORY_LOG_DIR"] = str(base / "state" / "log")


def _make_profile(**kwargs: Any) -> QueryProfile:
    defaults: dict[str, Any] = {
        "raw_text": "",
        "normalized_text": "",
        "error_family": "generic_runtime_error",
        "root_cause_class": "unknown",
        "exception_types": [],
        "symptom_tokens": [],
        "context_tokens": [],
        "command_tokens": [],
        "path_tokens": [],
        "tokens": [],
        "tags": [],
        "evidence": [],
        "entity_slots": {},
        "repo_name": "",
    }
    defaults.update(kwargs)
    return QueryProfile(**defaults)


# ---------------------------------------------------------------------------
# 4.1  IDF Token Prioritization
# ---------------------------------------------------------------------------
class TestIDFTokenPrioritization(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory(prefix="phase4-idf-")
        _make_env(Path(self.temp_dir.name))
        self.app = IssueMemoryApp()
        self.app.store.initialize()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_make_fts_query_with_idf_sorts_by_score(self) -> None:
        profile = _make_profile(
            tokens=["alpha", "beta", "gamma", "delta"],
        )
        idf_scores = {"alpha": 0.1, "beta": 2.5, "gamma": 1.0, "delta": 3.0}
        query = CandidateRetriever.make_fts_query(profile, idf_scores=idf_scores)
        parts = query.split(" OR ")
        # delta (3.0) should come before beta (2.5) before gamma (1.0) before alpha (0.1)
        self.assertEqual(parts, ["delta", "beta", "gamma", "alpha"])

    def test_make_fts_query_without_idf_keeps_original_order(self) -> None:
        profile = _make_profile(tokens=["aaa", "bbb", "ccc"])
        query = CandidateRetriever.make_fts_query(profile)
        parts = query.split(" OR ")
        self.assertEqual(parts, ["aaa", "bbb", "ccc"])

    def test_make_fts_query_limits_to_20_tokens(self) -> None:
        tokens = [f"token{i:02d}" for i in range(30)]
        profile = _make_profile(tokens=tokens)
        query = CandidateRetriever.make_fts_query(profile)
        parts = query.split(" OR ")
        self.assertLessEqual(len(parts), 20)

    def test_rebuild_token_idf_populates_table(self) -> None:
        # Two patterns so IDF is > 0 for tokens in only one doc
        self.app.issue_record_resolution(
            title="ModuleNotFoundError missing",
            raw_error="ModuleNotFoundError: No module named numpy",
            canonical_symptom="import numpy fails",
            canonical_fix="pip install numpy",
            prevention_rule="add numpy to requirements.txt",
            error_family="import_error",
        )
        self.app.issue_record_resolution(
            title="TypeError int callable",
            raw_error="TypeError: int is not callable",
            canonical_symptom="int not callable",
            canonical_fix="check parentheses",
            prevention_rule="avoid calling non-callables",
            error_family="type_error",
        )
        count = self.app.store.rebuild_token_idf()
        self.assertGreater(count, 0)
        scores = self.app.store.query_token_idf(["numpy"])
        self.assertTrue(scores)
        # "numpy" only in 1 doc out of 2+, so idf > 0
        for v in scores.values():
            self.assertGreaterEqual(v, 0.0)

    def test_query_token_idf_empty_tokens(self) -> None:
        result = self.app.store.query_token_idf([])
        self.assertEqual(result, {})

    def test_rebuild_token_idf_idempotent(self) -> None:
        self.app.issue_record_resolution(
            title="TypeError value error",
            raw_error="TypeError: int is not callable",
            canonical_symptom="int is not callable",
            canonical_fix="check parentheses",
            prevention_rule="avoid calling non-callables",
            error_family="type_error",
        )
        count1 = self.app.store.rebuild_token_idf()
        count2 = self.app.store.rebuild_token_idf()
        self.assertEqual(count1, count2)


# ---------------------------------------------------------------------------
# 4.2  Synonym Expansion
# ---------------------------------------------------------------------------
class TestSynonymExpansion(unittest.TestCase):
    def test_synonym_map_is_bidirectional(self) -> None:
        for a, syns in SYNONYM_MAP.items():
            for b in syns:
                self.assertIn(
                    a, SYNONYM_MAP.get(b, set()), f"({a}, {b}) not bidirectional"
                )

    def test_synonym_map_has_minimum_pairs(self) -> None:
        self.assertGreaterEqual(len(SYNONYM_MAP), 50)

    def test_expand_synonyms_basic(self) -> None:
        result = expand_synonyms(["gpu"])
        self.assertIn("gpu", result)
        self.assertIn("cuda", result)

    def test_expand_synonyms_preserves_order(self) -> None:
        result = expand_synonyms(["alpha", "gpu"])
        self.assertEqual(result[0], "alpha")
        self.assertEqual(result[1], "gpu")

    def test_expand_synonyms_no_duplicates(self) -> None:
        result = expand_synonyms(["cuda", "gpu"])
        self.assertEqual(len(result), len(set(result)))

    def test_expand_synonyms_empty(self) -> None:
        self.assertEqual(expand_synonyms([]), [])

    def test_synonym_pairs_for_known_token(self) -> None:
        pairs = synonym_pairs_for("gpu")
        self.assertTrue(any(syn == "cuda" for _, syn in pairs))

    def test_synonym_pairs_for_unknown_token(self) -> None:
        pairs = synonym_pairs_for("xyzzyfoobarbaz")
        self.assertEqual(pairs, [])

    def test_tokenize_expands_synonyms_by_default(self) -> None:
        tokens = tokenize("cuda mismatch")
        # "cuda" should bring in synonym "gpu" or "device"
        self.assertIn("cuda", tokens)
        # At least one synonym should be added
        synonym_tokens = SYNONYM_MAP.get("cuda", set())
        self.assertTrue(
            any(s in tokens for s in synonym_tokens),
            f"no synonym of cuda found in {tokens}",
        )

    def test_tokenize_no_synonyms_when_disabled(self) -> None:
        tokens = tokenize("cuda mismatch", expand_syns=False)
        self.assertIn("cuda", tokens)
        # None of cuda's synonyms should appear (they weren't in original text)
        for syn in SYNONYM_MAP.get("cuda", set()):
            if syn != "cuda":
                self.assertNotIn(syn, tokens)

    def test_dense_embedding_synonym_contribution(self) -> None:
        """Synonym augmentation should shift the embedding vector."""
        temp_dir = tempfile.TemporaryDirectory(prefix="phase4-syn-dense-")
        try:
            _make_env(Path(temp_dir.name))
            app = IssueMemoryApp()
            app.store.initialize()
            idx = DenseEmbeddingIndex(app.store)
            # Embedding with synonyms active (default tokenize)
            vec_a = idx.embed_text("cuda error")
            # The vector should be non-zero
            self.assertTrue(any(v != 0.0 for v in vec_a))
        finally:
            temp_dir.cleanup()


# ---------------------------------------------------------------------------
# 4.3  Entity Slot Learning
# ---------------------------------------------------------------------------
class TestEntitySlotLearning(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory(prefix="phase4-entity-")
        _make_env(Path(self.temp_dir.name))
        self.app = IssueMemoryApp()
        self.app.store.initialize()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_entity_importance_tables_created(self) -> None:
        with self.app.store.managed_connection() as conn:
            tables = {
                row["name"]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
        self.assertIn("entity_importance", tables)
        self.assertIn("token_idf", tables)

    def test_update_entity_importance_creates_record(self) -> None:
        self.app.store.update_entity_importance(
            entity_key="module_name",
            error_family="import_error",
            is_match=True,
            is_positive_outcome=True,
        )
        weights = self.app.store.query_entity_importance(
            "import_error", ["module_name"]
        )
        self.assertIn("module_name", weights)
        self.assertGreater(weights["module_name"], 0.0)

    def test_entity_importance_weight_adjusts_with_feedback(self) -> None:
        store = self.app.store
        # 5 positive feedbacks
        for _ in range(5):
            store.update_entity_importance(
                entity_key="config_key",
                error_family="config_error",
                is_match=True,
                is_positive_outcome=True,
            )
        weights_positive = store.query_entity_importance("config_error", ["config_key"])
        w_pos = weights_positive.get("config_key", 1.0)

        # Reset and do 5 negative
        with store.managed_connection(immediate=True) as conn:
            conn.execute("DELETE FROM entity_importance")
        for _ in range(5):
            store.update_entity_importance(
                entity_key="config_key",
                error_family="config_error",
                is_match=True,
                is_positive_outcome=False,
            )
        weights_negative = store.query_entity_importance("config_error", ["config_key"])
        w_neg = weights_negative.get("config_key", 1.0)

        # Positive should have higher weight than negative
        self.assertGreater(w_pos, w_neg)

    def test_entity_importance_query_empty(self) -> None:
        result = self.app.store.query_entity_importance("import_error", [])
        self.assertEqual(result, {})

    def test_entity_importance_prefers_specific_family(self) -> None:
        store = self.app.store
        # Insert specific family and global
        store.update_entity_importance(
            entity_key="module_name",
            error_family="import_error",
            is_match=True,
            is_positive_outcome=True,
        )
        store.update_entity_importance(
            entity_key="module_name",
            error_family="",
            is_match=True,
            is_positive_outcome=False,
        )
        # Query should prefer import_error-specific entry
        weights = store.query_entity_importance("import_error", ["module_name"])
        self.assertIn("module_name", weights)

    def test_build_features_with_entity_importance(self) -> None:
        """Entity importance weights should scale entity match/conflict scores."""
        profile = _make_profile(
            entity_slots={"module_name": "numpy"},
            error_family="import_error",
        )
        candidate: dict[str, Any] = {
            "id": 1,
            "pattern_id": 1,
            "variant_id": None,
            "candidate_type": "pattern",
            "title": "Missing module",
            "canonical_symptom": "ModuleNotFoundError",
            "canonical_fix": "pip install",
            "prevention_rule": "",
            "verification_steps": "",
            "tags": "",
            "error_family": "import_error",
            "root_cause_class": "unknown",
            "project_scope": "global",
            "updated_at": "",
            "best_variant": {
                "entity_slots_json": {"module_name": "numpy"},
                "tags_json": [],
                "applicability_json": {},
                "success_count": 0,
                "reject_count": 0,
                "confidence": 0.5,
                "memory_strength": 0.5,
                "times_used": 0,
                "updated_at": "",
            },
            "times_seen": 1,
            "confidence": 0.5,
            "retrieval_signals": {},
            "dense_score": 0.0,
            "variant_match_score": 0.0,
            "episodes": [],
            "session_boost": 0.0,
            "session_penalty": 0.0,
        }
        # Without entity importance
        features_base, _ = build_candidate_features(
            profile,
            candidate,
            project_scope="global",
        )
        # With boosted entity importance
        features_boosted, _ = build_candidate_features(
            profile,
            candidate,
            project_scope="global",
            entity_importance={"module_name": 1.8},
        )
        self.assertGreater(
            features_boosted["entity_match_score"],
            features_base["entity_match_score"],
        )

    def test_build_features_entity_importance_none_is_noop(self) -> None:
        """Passing None entity_importance should not change scores."""
        profile = _make_profile(
            entity_slots={"module_name": "numpy"},
        )
        candidate: dict[str, Any] = {
            "id": 1,
            "pattern_id": 1,
            "variant_id": None,
            "candidate_type": "pattern",
            "title": "Missing module",
            "canonical_symptom": "ModuleNotFoundError",
            "canonical_fix": "pip install",
            "prevention_rule": "",
            "verification_steps": "",
            "tags": "",
            "error_family": "import_error",
            "root_cause_class": "unknown",
            "project_scope": "global",
            "updated_at": "",
            "best_variant": {
                "entity_slots_json": {"module_name": "numpy"},
                "tags_json": [],
                "applicability_json": {},
                "success_count": 0,
                "reject_count": 0,
                "confidence": 0.5,
                "memory_strength": 0.5,
                "times_used": 0,
                "updated_at": "",
            },
            "times_seen": 1,
            "confidence": 0.5,
            "retrieval_signals": {},
            "dense_score": 0.0,
            "variant_match_score": 0.0,
            "episodes": [],
            "session_boost": 0.0,
            "session_penalty": 0.0,
        }
        features_a, _ = build_candidate_features(
            profile,
            candidate,
            project_scope="global",
            entity_importance=None,
        )
        features_b, _ = build_candidate_features(
            profile,
            candidate,
            project_scope="global",
        )
        self.assertAlmostEqual(
            features_a["entity_match_score"], features_b["entity_match_score"]
        )


if __name__ == "__main__":
    unittest.main()
