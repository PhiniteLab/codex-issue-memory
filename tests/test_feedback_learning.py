from __future__ import annotations

import os
import tempfile
from pathlib import Path
import unittest

from codex_issue_memory.app import IssueMemoryApp


class FeedbackLearningTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory(prefix="issue-memory-feedback-")
        base = Path(self.temp_dir.name)
        os.environ["ISSUE_MEMORY_HOME"] = str(base / "share")
        os.environ["ISSUE_MEMORY_DB_PATH"] = str(base / "share" / "issue_memory.sqlite3")
        os.environ["ISSUE_MEMORY_STATE_DIR"] = str(base / "state")
        os.environ["ISSUE_MEMORY_BACKUP_DIR"] = str(base / "share" / "backups")
        os.environ["ISSUE_MEMORY_LOG_DIR"] = str(base / "state" / "log")
        self.app = IssueMemoryApp()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_match_logs_telemetry_and_verified_feedback_updates_variant_and_thompson_stats(self) -> None:
        stored = self.app.issue_record_resolution(
            title="Relative sqlite path breaks outside repo root",
            raw_error="FileNotFoundError: references/contractsDatabase.sqlite3",
            canonical_fix="Resolve the SQLite path relative to __file__.",
            prevention_rule="No production DB path may depend on cwd.",
            project_scope="global",
            canonical_symptom="sqlite database path fails outside repo root",
            verification_steps="Run from repo root and external cwd.",
            tags="sqlite,path,cwd",
            error_family="sqlite_error",
            root_cause_class="cwd_relative_path_bug",
            command="python -m app.main",
            file_path="services/db_loader.py",
            stack_excerpt='File "services/db_loader.py", line 12, in load_db',
            domain="python",
            session_id="session-telemetry",
        )

        seeded_variant_stat = self.app.store.get_variant_stat(stored["variant_id"])
        seeded_strategy_stat = self.app.store.get_strategy_stat(
            scope_type="global",
            scope_key="",
            strategy_key="resolve_from___file__",
        )
        self.assertIsNotNone(seeded_variant_stat)
        self.assertIsNotNone(seeded_strategy_stat)
        assert seeded_variant_stat is not None
        assert seeded_strategy_stat is not None
        self.assertEqual(seeded_variant_stat["success_count"], 1)
        self.assertEqual(seeded_strategy_stat["success_count"], 1)

        match = self.app.issue_match(
            error_text="FileNotFoundError: references/contractsDatabase.sqlite3 while running python -m app.main from another directory",
            command="python -m app.main",
            file_path="services/db_loader.py",
            project_scope="global",
            session_id="session-telemetry",
            limit=3,
        )
        self.assertIsInstance(match["retrieval_event_id"], int)
        self.assertTrue(match["matches"])
        self.assertIsInstance(match["matches"][0].get("retrieval_candidate_id"), int)
        self.assertEqual(match["matches"][0].get("variant_id"), stored["variant_id"])

        with self.app.store.managed_connection() as conn:
            retrieval_event = conn.execute(
                "SELECT * FROM retrieval_events WHERE id = ?",
                (match["retrieval_event_id"],),
            ).fetchone()
            candidate_rows = conn.execute(
                "SELECT * FROM retrieval_candidates WHERE retrieval_event_id = ? ORDER BY candidate_rank ASC",
                (match["retrieval_event_id"],),
            ).fetchall()

        self.assertIsNotNone(retrieval_event)
        assert retrieval_event is not None
        self.assertEqual(int(retrieval_event["selected_pattern_id"]), stored["pattern_id"])
        self.assertEqual(int(retrieval_event["selected_variant_id"]), stored["variant_id"])
        self.assertGreaterEqual(len(candidate_rows), 1)
        self.assertEqual(str(candidate_rows[0]["candidate_type"]), "variant")

        feedback = self.app.issue_feedback(
            retrieval_event_id=match["retrieval_event_id"],
            feedback_type="fix_verified",
            candidate_rank=1,
            notes="Verified by rerunning the failing module from an external cwd.",
        )
        self.assertEqual(feedback["feedback_type"], "fix_verified")
        self.assertTrue(feedback["global_update_applied"])
        self.assertGreater(feedback["variant_update"]["confidence_after"], feedback["variant_update"]["confidence_before"])
        self.assertEqual(feedback["variant_stat_update"]["success_count"], 2)
        self.assertEqual(len(feedback["strategy_stat_updates"]), 1)
        self.assertEqual(feedback["strategy_stat_updates"][0]["scope_type"], "global")
        self.assertIsNone(feedback["learning"])
        self.assertIsNone(feedback["bandit"])

        bundle = self.app.issue_get(pattern_id=stored["pattern_id"], include_examples=True, examples_limit=10)
        self.assertEqual(bundle["variants"][0]["success_count"], 2)
        self.assertGreater(bundle["variants"][0]["confidence"], 0.78)

        with self.app.store.managed_connection() as conn:
            feedback_count = conn.execute("SELECT COUNT(*) AS count FROM feedback_events").fetchone()["count"]

        updated_variant_stat = self.app.store.get_variant_stat(stored["variant_id"])
        updated_strategy_stat = self.app.store.get_strategy_stat(
            scope_type="global",
            scope_key="",
            strategy_key="resolve_from___file__",
        )
        self.assertEqual(int(feedback_count), 1)
        assert updated_variant_stat is not None
        assert updated_strategy_stat is not None
        self.assertEqual(updated_variant_stat["success_count"], 2)
        self.assertEqual(updated_strategy_stat["success_count"], 2)

    def test_weak_feedback_is_session_only_and_verified_feedback_updates_global_stats(self) -> None:
        stored = self.app.issue_record_resolution(
            title="Relative sqlite path breaks outside repo root",
            raw_error="FileNotFoundError: references/contractsDatabase.sqlite3",
            canonical_fix="Resolve the SQLite path relative to __file__.",
            prevention_rule="No production DB path may depend on cwd.",
            project_scope="global",
            canonical_symptom="sqlite database path fails outside repo root",
            verification_steps="Run from repo root and external cwd.",
            tags="sqlite,path,cwd",
            error_family="sqlite_error",
            root_cause_class="cwd_relative_path_bug",
            command="python -m app.main",
            file_path="services/db_loader.py",
            stack_excerpt='File "services/db_loader.py", line 12, in load_db',
            domain="python",
        )
        query = "FileNotFoundError: references/contractsDatabase.sqlite3 while running python -m app.main from another directory"

        first = self.app.issue_match(
            error_text=query,
            command="python -m app.main",
            file_path="services/db_loader.py",
            project_scope="global",
            session_id="learning-a",
            limit=3,
        )
        score_before = first["matches"][0]["score"]
        self.assertEqual(first["decision"]["status"], "match")

        negative = self.app.issue_feedback(
            retrieval_event_id=first["retrieval_event_id"],
            feedback_type="candidate_rejected",
            candidate_rank=1,
            notes="Synthetic negative feedback to test session-only routing.",
        )
        self.assertFalse(negative["global_update_applied"])
        self.assertIsNone(negative["pattern_update"])
        self.assertIsNone(negative["variant_update"])
        self.assertIsNone(negative["variant_stat_update"])
        self.assertEqual(negative["strategy_stat_updates"], [])

        same_session = self.app.issue_match(
            error_text=query,
            command="python -m app.main",
            file_path="services/db_loader.py",
            project_scope="global",
            session_id="learning-a",
            limit=3,
        )
        score_same_session = same_session["matches"][0]["score"]
        self.assertLess(score_same_session, score_before)
        self.assertIn("session-rejection-memory", same_session["matches"][0]["why"])

        other_session = self.app.issue_match(
            error_text=query,
            command="python -m app.main",
            file_path="services/db_loader.py",
            project_scope="global",
            session_id="learning-b",
            limit=3,
        )
        score_other_session = other_session["matches"][0]["score"]
        self.assertAlmostEqual(score_other_session, score_before, places=3)
        self.assertNotIn("session-rejection-memory", other_session["matches"][0]["why"])

        weak_variant_stat = self.app.store.get_variant_stat(stored["variant_id"])
        assert weak_variant_stat is not None
        self.assertEqual(weak_variant_stat["success_count"], 1)
        self.assertEqual(weak_variant_stat["failure_count"], 0)

        positive = self.app.issue_feedback(
            retrieval_event_id=other_session["retrieval_event_id"],
            feedback_type="fix_verified",
            candidate_rank=1,
            notes="Synthetic positive feedback to test verified-only routing.",
        )
        self.assertTrue(positive["global_update_applied"])
        self.assertEqual(positive["variant_stat_update"]["success_count"], 2)

        final_match = self.app.issue_match(
            error_text=query,
            command="python -m app.main",
            file_path="services/db_loader.py",
            project_scope="global",
            session_id="learning-c",
            limit=3,
        )
        self.assertGreaterEqual(final_match["matches"][0]["score"], score_before)
        self.assertEqual(final_match["decision"]["status"], "match")

    def test_record_resolution_persists_user_scope_strategy_entities_and_seeded_stats(self) -> None:
        stored = self.app.issue_record_resolution(
            title="Relative sqlite path breaks outside repo root",
            raw_error="FileNotFoundError: references/contractsDatabase.sqlite3",
            canonical_fix="Resolve the SQLite path relative to __file__.",
            prevention_rule="No production DB path may depend on cwd.",
            project_scope="global",
            user_scope="mehmet",
            command="python -m app.main",
            file_path="services/db_loader.py",
            resolution_notes="Keep the path anchored to __file__ to avoid cwd drift.",
        )
        bundle = self.app.issue_get(pattern_id=stored["pattern_id"], include_examples=True, examples_limit=5)
        self.assertEqual(bundle["variants"][0]["strategy_key"], "resolve_from___file__")
        self.assertEqual(bundle["episodes"][0]["user_scope"], "mehmet")
        self.assertIn("missing_path", bundle["variants"][0]["entity_slots_json"])
        self.assertIn("resolve_from___file__", bundle["variants"][0]["strategy_hints_json"])

        global_strategy = self.app.store.get_strategy_stat(
            scope_type="global",
            scope_key="",
            strategy_key="resolve_from___file__",
        )
        user_strategy = self.app.store.get_strategy_stat(
            scope_type="user",
            scope_key="mehmet",
            strategy_key="resolve_from___file__",
        )
        variant_stat = self.app.store.get_variant_stat(stored["variant_id"])
        self.assertIsNotNone(global_strategy)
        self.assertIsNotNone(user_strategy)
        self.assertIsNotNone(variant_stat)
        assert global_strategy is not None
        assert user_strategy is not None
        assert variant_stat is not None
        self.assertEqual(global_strategy["success_count"], 1)
        self.assertEqual(user_strategy["success_count"], 1)
        self.assertEqual(variant_stat["success_count"], 1)

    def test_false_positive_updates_negative_applicability_and_failure_stats(self) -> None:
        stored = self.app.issue_record_resolution(
            title="Relative sqlite path breaks outside repo root",
            raw_error="FileNotFoundError: references/contractsDatabase.sqlite3",
            canonical_fix="Resolve the SQLite path relative to __file__.",
            prevention_rule="No production DB path may depend on cwd.",
            project_scope="global",
            user_scope="mehmet",
            repo_name="repo-alpha",
            command="python -m app.main",
            file_path="services/db_loader.py",
        )
        match = self.app.issue_match(
            error_text="FileNotFoundError: references/contractsDatabase.sqlite3 while running python -m app.main from another directory",
            command="python -m app.main",
            file_path="services/db_loader.py",
            project_scope="global",
            user_scope="mehmet",
            repo_name="repo-alpha",
            session_id="false-positive-session",
            limit=3,
        )
        feedback = self.app.issue_feedback(
            retrieval_event_id=match["retrieval_event_id"],
            feedback_type="false_positive",
            candidate_rank=1,
            notes="This exact fix was incorrect in repo-alpha.",
        )
        self.assertTrue(feedback["global_update_applied"])
        self.assertTrue(feedback["negative_applicability_applied"])
        self.assertEqual(feedback["variant_stat_update"]["failure_count"], 1)

        bundle = self.app.issue_get(pattern_id=stored["pattern_id"], include_examples=True, examples_limit=5)
        negative_applicability = bundle["variants"][0]["negative_applicability_json"]
        self.assertEqual(negative_applicability["false_positive_count"], 1)
        self.assertIn("repo-alpha", negative_applicability["repo_names"])
        self.assertIn("mehmet", negative_applicability["user_scopes"])

        repo_strategy = self.app.store.get_strategy_stat(
            scope_type="repo",
            scope_key="repo-alpha",
            strategy_key="resolve_from___file__",
        )
        user_strategy = self.app.store.get_strategy_stat(
            scope_type="user",
            scope_key="mehmet",
            strategy_key="resolve_from___file__",
        )
        variant_stat = self.app.store.get_variant_stat(stored["variant_id"])
        assert repo_strategy is not None
        assert user_strategy is not None
        assert variant_stat is not None
        self.assertEqual(repo_strategy["failure_count"], 1)
        self.assertEqual(user_strategy["failure_count"], 1)
        self.assertEqual(variant_stat["failure_count"], 1)


if __name__ == "__main__":
    unittest.main()
