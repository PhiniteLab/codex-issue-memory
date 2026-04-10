from __future__ import annotations

import os
import tempfile
from pathlib import Path
import unittest

from codex_issue_memory.app import IssueMemoryApp


def _make_app() -> IssueMemoryApp:
    return IssueMemoryApp()


def _seed_pattern(app: IssueMemoryApp) -> dict:
    return app.issue_record_resolution(
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
        session_id="phase0-seed",
    )


def _do_match(app: IssueMemoryApp, session_id: str = "phase0-test") -> dict:
    return app.issue_match(
        error_text="FileNotFoundError: references/contractsDatabase.sqlite3 while running python -m app.main from another directory",
        command="python -m app.main",
        file_path="services/db_loader.py",
        project_scope="global",
        session_id=session_id,
        limit=3,
    )


class Phase01WeakFeedbackLearningTests(unittest.TestCase):
    """Phase 0.1: All feedback types update variant stats with appropriate weights."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory(prefix="issue-memory-phase01-")
        base = Path(self.temp_dir.name)
        os.environ["ISSUE_MEMORY_HOME"] = str(base / "share")
        os.environ["ISSUE_MEMORY_DB_PATH"] = str(
            base / "share" / "issue_memory.sqlite3"
        )
        os.environ["ISSUE_MEMORY_STATE_DIR"] = str(base / "state")
        os.environ["ISSUE_MEMORY_BACKUP_DIR"] = str(base / "share" / "backups")
        os.environ["ISSUE_MEMORY_LOG_DIR"] = str(base / "state" / "log")
        self.app = _make_app()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_candidate_accepted_updates_variant_stat_with_fractional_weight(
        self,
    ) -> None:
        stored = _seed_pattern(self.app)
        match = _do_match(self.app)
        stat_before = self.app.store.get_variant_stat(stored["variant_id"])
        assert stat_before is not None

        fb = self.app.issue_feedback(
            retrieval_event_id=match["retrieval_event_id"],
            feedback_type="candidate_accepted",
            candidate_rank=1,
        )
        self.assertFalse(fb["global_update_applied"])
        self.assertIsNone(fb["pattern_update"])
        self.assertIsNone(fb["variant_update"])
        self.assertIsNotNone(fb["variant_stat_update"])

        stat_after = self.app.store.get_variant_stat(stored["variant_id"])
        assert stat_after is not None
        self.assertEqual(stat_after["success_count"], stat_before["success_count"] + 1)
        self.assertGreater(float(stat_after["alpha"]), float(stat_before["alpha"]))
        alpha_delta = float(stat_after["alpha"]) - float(stat_before["alpha"])
        self.assertAlmostEqual(alpha_delta, 0.35, places=1)

    def test_candidate_rejected_updates_variant_stat_with_fractional_weight(
        self,
    ) -> None:
        stored = _seed_pattern(self.app)
        match = _do_match(self.app)
        stat_before = self.app.store.get_variant_stat(stored["variant_id"])
        assert stat_before is not None

        fb = self.app.issue_feedback(
            retrieval_event_id=match["retrieval_event_id"],
            feedback_type="candidate_rejected",
            candidate_rank=1,
        )
        self.assertIsNotNone(fb["variant_stat_update"])

        stat_after = self.app.store.get_variant_stat(stored["variant_id"])
        assert stat_after is not None
        self.assertEqual(stat_after["failure_count"], stat_before["failure_count"] + 1)
        beta_delta = float(stat_after["beta"]) - float(stat_before["beta"])
        self.assertAlmostEqual(beta_delta, 0.25, places=1)

    def test_merge_confirmed_updates_variant_stat(self) -> None:
        stored = _seed_pattern(self.app)
        match = _do_match(self.app)

        fb = self.app.issue_feedback(
            retrieval_event_id=match["retrieval_event_id"],
            feedback_type="merge_confirmed",
            candidate_rank=1,
        )
        self.assertIsNotNone(fb["variant_stat_update"])
        stat = self.app.store.get_variant_stat(stored["variant_id"])
        assert stat is not None
        self.assertEqual(stat["success_count"], 2)

    def test_split_rejected_updates_variant_stat(self) -> None:
        stored = _seed_pattern(self.app)
        match = _do_match(self.app)

        fb = self.app.issue_feedback(
            retrieval_event_id=match["retrieval_event_id"],
            feedback_type="split_rejected",
            candidate_rank=1,
        )
        self.assertIsNotNone(fb["variant_stat_update"])
        stat = self.app.store.get_variant_stat(stored["variant_id"])
        assert stat is not None
        self.assertEqual(stat["failure_count"], 1)

    def test_strong_feedback_still_updates_pattern_and_strategy(self) -> None:
        _seed_pattern(self.app)
        match = _do_match(self.app)

        fb = self.app.issue_feedback(
            retrieval_event_id=match["retrieval_event_id"],
            feedback_type="fix_verified",
            candidate_rank=1,
        )
        self.assertTrue(fb["global_update_applied"])
        self.assertIsNotNone(fb["pattern_update"])
        self.assertIsNotNone(fb["variant_update"])
        self.assertIsNotNone(fb["variant_stat_update"])
        self.assertGreater(len(fb["strategy_stat_updates"]), 0)

    def test_weak_feedback_does_not_update_pattern_or_strategy(self) -> None:
        _seed_pattern(self.app)
        _do_match(self.app)

        for weak_type in (
            "candidate_accepted",
            "candidate_rejected",
            "merge_confirmed",
            "split_confirmed",
        ):
            m = _do_match(self.app, session_id=f"weak-{weak_type}")
            fb = self.app.issue_feedback(
                retrieval_event_id=m["retrieval_event_id"],
                feedback_type=weak_type,
                candidate_rank=1,
            )
            self.assertFalse(
                fb["global_update_applied"],
                msg=f"{weak_type} should not set global_update_applied",
            )
            self.assertIsNone(
                fb["pattern_update"], msg=f"{weak_type} should not update pattern"
            )
            self.assertIsNone(
                fb["variant_update"],
                msg=f"{weak_type} should not update variant confidence",
            )
            self.assertEqual(
                fb["strategy_stat_updates"],
                [],
                msg=f"{weak_type} should not update strategy",
            )
            self.assertIsNotNone(
                fb["variant_stat_update"], msg=f"{weak_type} should update variant_stat"
            )


class Phase02ProvenScoreTests(unittest.TestCase):
    """Phase 0.2: proven_score feature is computed and influences ranking."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory(prefix="issue-memory-phase02-")
        base = Path(self.temp_dir.name)
        os.environ["ISSUE_MEMORY_HOME"] = str(base / "share")
        os.environ["ISSUE_MEMORY_DB_PATH"] = str(
            base / "share" / "issue_memory.sqlite3"
        )
        os.environ["ISSUE_MEMORY_STATE_DIR"] = str(base / "state")
        os.environ["ISSUE_MEMORY_BACKUP_DIR"] = str(base / "share" / "backups")
        os.environ["ISSUE_MEMORY_LOG_DIR"] = str(base / "state" / "log")
        self.app = _make_app()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_proven_score_appears_in_candidate_features(self) -> None:
        _seed_pattern(self.app)
        match = _do_match(self.app)
        self.assertTrue(match["matches"])
        with self.app.store.managed_connection() as conn:
            row = conn.execute(
                "SELECT feature_json FROM retrieval_candidates WHERE retrieval_event_id = ? ORDER BY candidate_rank LIMIT 1",
                (match["retrieval_event_id"],),
            ).fetchone()
        self.assertIsNotNone(row)
        features = self.app.store.decode_feature_json(row["feature_json"])
        self.assertIn("proven_score", features)

    def test_proven_score_increases_after_verified_feedback(self) -> None:
        _seed_pattern(self.app)

        m1 = _do_match(self.app, session_id="proven-1")
        with self.app.store.managed_connection() as conn:
            row1 = conn.execute(
                "SELECT feature_json FROM retrieval_candidates WHERE retrieval_event_id = ? ORDER BY candidate_rank LIMIT 1",
                (m1["retrieval_event_id"],),
            ).fetchone()
        features1 = self.app.store.decode_feature_json(row1["feature_json"])
        proven_before = features1["proven_score"]

        self.app.issue_feedback(
            retrieval_event_id=m1["retrieval_event_id"],
            feedback_type="fix_verified",
            candidate_rank=1,
        )

        m2 = _do_match(self.app, session_id="proven-2")
        with self.app.store.managed_connection() as conn:
            row2 = conn.execute(
                "SELECT feature_json FROM retrieval_candidates WHERE retrieval_event_id = ? ORDER BY candidate_rank LIMIT 1",
                (m2["retrieval_event_id"],),
            ).fetchone()
        features2 = self.app.store.decode_feature_json(row2["feature_json"])
        proven_after = features2["proven_score"]

        self.assertGreater(proven_after, proven_before)

    def test_support_score_weight_increased(self) -> None:
        from codex_issue_memory.retrieval.ranker import HeuristicRanker

        self.assertEqual(HeuristicRanker.DEFAULT_WEIGHTS["support_score"], 0.05)

    def test_proven_score_weight_exists(self) -> None:
        from codex_issue_memory.retrieval.ranker import HeuristicRanker

        self.assertEqual(HeuristicRanker.DEFAULT_WEIGHTS["proven_score"], 0.08)


class Phase03FeatureOutcomeLogTests(unittest.TestCase):
    """Phase 0.3: Feature-outcome correlation logging."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory(prefix="issue-memory-phase03-")
        base = Path(self.temp_dir.name)
        os.environ["ISSUE_MEMORY_HOME"] = str(base / "share")
        os.environ["ISSUE_MEMORY_DB_PATH"] = str(
            base / "share" / "issue_memory.sqlite3"
        )
        os.environ["ISSUE_MEMORY_STATE_DIR"] = str(base / "state")
        os.environ["ISSUE_MEMORY_BACKUP_DIR"] = str(base / "share" / "backups")
        os.environ["ISSUE_MEMORY_LOG_DIR"] = str(base / "state" / "log")
        self.app = _make_app()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_feedback_logs_feature_outcomes(self) -> None:
        _seed_pattern(self.app)
        match = _do_match(self.app)

        fb = self.app.issue_feedback(
            retrieval_event_id=match["retrieval_event_id"],
            feedback_type="fix_verified",
            candidate_rank=1,
        )
        self.assertGreater(fb["feature_log_count"], 0)

        with self.app.store.managed_connection() as conn:
            rows = conn.execute("SELECT * FROM feature_outcome_log").fetchall()
        self.assertGreater(len(rows), 0)
        feature_names = {r["feature_name"] for r in rows}
        self.assertIn("root_score", feature_names)
        self.assertIn("proven_score", feature_names)
        self.assertIn("feedback_score", feature_names)
        for row in rows:
            self.assertEqual(row["feedback_type"], "fix_verified")
            self.assertGreater(row["reward"], 0)

    def test_weak_feedback_also_logs_feature_outcomes(self) -> None:
        _seed_pattern(self.app)
        match = _do_match(self.app)

        fb = self.app.issue_feedback(
            retrieval_event_id=match["retrieval_event_id"],
            feedback_type="candidate_rejected",
            candidate_rank=1,
        )
        self.assertGreater(fb["feature_log_count"], 0)

        with self.app.store.managed_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM feature_outcome_log WHERE feedback_type = 'candidate_rejected'"
            ).fetchall()
        self.assertGreater(len(rows), 0)

    def test_query_feature_outcome_stats_returns_aggregated_data(self) -> None:
        _seed_pattern(self.app)
        m1 = _do_match(self.app, session_id="stats-1")
        self.app.issue_feedback(
            retrieval_event_id=m1["retrieval_event_id"],
            feedback_type="fix_verified",
            candidate_rank=1,
        )
        m2 = _do_match(self.app, session_id="stats-2")
        self.app.issue_feedback(
            retrieval_event_id=m2["retrieval_event_id"],
            feedback_type="candidate_rejected",
            candidate_rank=1,
        )

        stats = self.app.store.query_feature_outcome_stats()
        self.assertGreater(len(stats), 0)
        stat_names = {s["feature_name"] for s in stats}
        self.assertIn("root_score", stat_names)
        for stat in stats:
            self.assertGreater(stat["sample_count"], 0)

    def test_feature_outcome_log_table_created_by_migration(self) -> None:
        with self.app.store.managed_connection() as conn:
            tables = [
                r["name"]
                for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            ]
        self.assertIn("feature_outcome_log", tables)

    def test_analyze_feature_importance_command_exists(self) -> None:
        from codex_issue_memory.maintenance import build_parser

        parser = build_parser()
        args = parser.parse_args(["analyze-feature-importance"])
        self.assertEqual(args.command, "analyze-feature-importance")


if __name__ == "__main__":
    unittest.main()
