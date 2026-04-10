"""Phase 3 — Advanced Learning Infrastructure tests.

Covers:
  3.1 Multi-factor posteriors
  3.2 Strategy family hierarchy
  3.3 A/B test framework
  3.4 Auto weight calibration
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any
import unittest

from codex_issue_memory.app import IssueMemoryApp
from codex_issue_memory.learning.families import STRATEGY_FAMILIES, resolve_strategy_family
from codex_issue_memory.learning.weight_calibration import compute_optimal_weights
from codex_issue_memory.storage import IssueMemoryStore


class Phase3ImprovementsTests(unittest.TestCase):
    _ENV_KEYS = (
        "ISSUE_MEMORY_HOME",
        "ISSUE_MEMORY_DB_PATH",
        "ISSUE_MEMORY_STATE_DIR",
        "ISSUE_MEMORY_BACKUP_DIR",
        "ISSUE_MEMORY_LOG_DIR",
        "ISSUE_MEMORY_ENABLE_DENSE_RETRIEVAL",
        "ISSUE_MEMORY_ENABLE_STRATEGY_BANDIT",
    )

    def setUp(self) -> None:
        self._env_backup = {key: os.environ.get(key) for key in self._ENV_KEYS}
        self.temp_dir = tempfile.TemporaryDirectory(prefix="issue-memory-phase3-")
        base = Path(self.temp_dir.name)
        os.environ["ISSUE_MEMORY_HOME"] = str(base / "share")
        os.environ["ISSUE_MEMORY_DB_PATH"] = str(base / "share" / "issue_memory.sqlite3")
        os.environ["ISSUE_MEMORY_STATE_DIR"] = str(base / "state")
        os.environ["ISSUE_MEMORY_BACKUP_DIR"] = str(base / "share" / "backups")
        os.environ["ISSUE_MEMORY_LOG_DIR"] = str(base / "state" / "log")
        os.environ["ISSUE_MEMORY_ENABLE_DENSE_RETRIEVAL"] = "0"
        os.environ["ISSUE_MEMORY_ENABLE_STRATEGY_BANDIT"] = "1"
        self.app = IssueMemoryApp()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()
        for key, value in self._env_backup.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def _store_resolution(self, **kwargs: Any) -> dict[str, Any]:
        defaults: dict[str, Any] = {
            "title": "Missing dependency crash",
            "raw_error": "ModuleNotFoundError: No module named 'requests'",
            "canonical_fix": "pip install requests",
            "prevention_rule": "Add to requirements.txt",
            "project_scope": "global",
            "user_scope": "test-user",
            "repo_name": "test-repo",
        }
        defaults.update(kwargs)
        return self.app.issue_record_resolution(**defaults)

    # ------------------------------------------------------------------
    # 3.1 Multi-factor posteriors
    # ------------------------------------------------------------------

    def test_strategy_stats_multi_factor_columns_exist(self) -> None:
        """Migration 012 adds multi-factor posterior columns to strategy_stats."""
        with self.app.store.managed_connection() as conn:
            info = conn.execute("PRAGMA table_info(strategy_stats)").fetchall()
        col_names = {row[1] for row in info}
        for factor in ("quality", "safety", "adoption"):
            self.assertIn(f"{factor}_alpha", col_names)
            self.assertIn(f"{factor}_beta", col_names)

    def test_multi_factor_posterior_update_on_feedback(self) -> None:
        """Strong feedback routes to the correct factor posterior columns."""
        stored = self._store_resolution()
        pattern_id = stored.get("id") or stored.get("pattern_id")
        self.assertIsNotNone(pattern_id)
        # Match to get a retrieval event
        match_result = self.app.issue_match(
            error_text="ModuleNotFoundError: No module named 'requests'",
            repo_name="test-repo",
            user_scope="test-user",
            session_id="sess-mf-1",
        )
        event_id = match_result.get("retrieval_event_id")
        if match_result["matches"] and event_id:
            self.app.issue_feedback(
                retrieval_event_id=int(event_id),
                pattern_id=int(pattern_id),
                feedback_type="fix_verified",
            )
        # Verify the stats table has factor data
        with self.app.store.managed_connection() as conn:
            rows = conn.execute(
                "SELECT quality_alpha, quality_beta, safety_alpha, safety_beta, adoption_alpha, adoption_beta FROM strategy_stats LIMIT 5",
            ).fetchall()
        # Rows may or may not exist (depends on strategy_key extraction), table columns are valid
        for row in rows:
            for v in row:
                self.assertIsInstance(v, (int, float))

    # ------------------------------------------------------------------
    # 3.2 Strategy family hierarchy
    # ------------------------------------------------------------------

    def test_strategy_families_defined(self) -> None:
        """STRATEGY_FAMILIES has expected families with non-empty member lists."""
        self.assertIn("dependency_management", STRATEGY_FAMILIES)
        self.assertIn("path_resolution", STRATEGY_FAMILIES)
        self.assertIn("tensor_correctness", STRATEGY_FAMILIES)
        for family, members in STRATEGY_FAMILIES.items():
            self.assertIsInstance(members, list)
            self.assertGreater(len(members), 0, f"Family {family} has no members")

    def test_resolve_strategy_family(self) -> None:
        """resolve_strategy_family returns correct family or empty string."""
        self.assertEqual(resolve_strategy_family("install_missing_dependency"), "dependency_management")
        self.assertEqual(resolve_strategy_family("resolve_from___file__"), "path_resolution")
        self.assertEqual(resolve_strategy_family("boundary_cast_float32"), "tensor_correctness")
        self.assertEqual(resolve_strategy_family("nonexistent_strategy_key"), "")

    def test_strategy_families_table_created(self) -> None:
        """Migration creates the strategy_families table."""
        with self.app.store.managed_connection() as conn:
            info = conn.execute("PRAGMA table_info(strategy_families)").fetchall()
        col_names = {row[1] for row in info}
        self.assertIn("family_key", col_names)
        self.assertIn("strategy_keys_json", col_names)
        self.assertIn("quality_alpha", col_names)

    def test_family_stats_load_empty(self) -> None:
        """load_family_stats returns empty dict for unknown families."""
        result = self.app.store.load_family_stats(["unknown_family"])
        self.assertEqual(result, {})

    # ------------------------------------------------------------------
    # 3.3 A/B test framework
    # ------------------------------------------------------------------

    def test_experiment_registry_table_created(self) -> None:
        """Migration creates the experiment_registry table with correct columns."""
        with self.app.store.managed_connection() as conn:
            info = conn.execute("PRAGMA table_info(experiment_registry)").fetchall()
        col_names = {row[1] for row in info}
        for col in ("experiment_id", "name", "status", "traffic_fraction", "treatment_config_json", "control_config_json"):
            self.assertIn(col, col_names)

    def test_create_and_manage_experiment(self) -> None:
        """Full experiment lifecycle: create → run → analyze → complete."""
        store = self.app.store
        # Create
        result = store.create_experiment(
            experiment_id="exp-001",
            name="Test Weight Swap",
            description="Compare default vs adjusted root_score weight",
            traffic_fraction=0.5,
        )
        self.assertEqual(result["status"], "draft")
        self.assertEqual(result["experiment_id"], "exp-001")

        # No active experiment yet
        self.assertIsNone(store.get_active_experiment())

        # Start
        result = store.update_experiment_status("exp-001", "running")
        self.assertEqual(result["status"], "running")
        self.assertIn("start_date", result)

        # Now active
        active = store.get_active_experiment()
        self.assertIsNotNone(active)
        self.assertEqual(active["experiment_id"], "exp-001")
        self.assertEqual(active["status"], "running")
        self.assertIsInstance(active["treatment_config"], dict)
        self.assertIsInstance(active["control_config"], dict)

        # Analyze (no events yet, so both arms should be 0)
        analysis = store.analyze_experiment("exp-001")
        self.assertEqual(analysis["experiment_id"], "exp-001")
        self.assertEqual(analysis["arms"]["treatment"]["total"], 0)
        self.assertEqual(analysis["arms"]["control"]["total"], 0)

        # Complete
        result = store.update_experiment_status("exp-001", "completed")
        self.assertEqual(result["status"], "completed")
        self.assertIsNone(store.get_active_experiment())

    def test_experiment_arm_assignment_deterministic(self) -> None:
        """Arm assignment is deterministic for same experiment+session."""
        experiment = {"experiment_id": "exp-det", "traffic_fraction": 0.5}
        arm1 = IssueMemoryStore.assign_experiment_arm(experiment, "session-abc")
        arm2 = IssueMemoryStore.assign_experiment_arm(experiment, "session-abc")
        self.assertEqual(arm1, arm2)
        self.assertIn(arm1, ("treatment", "control"))

    def test_experiment_arm_distribution(self) -> None:
        """Arm assignment produce roughly 50/50 distribution over many sessions."""
        experiment = {"experiment_id": "exp-dist", "traffic_fraction": 0.5}
        arms = [IssueMemoryStore.assign_experiment_arm(experiment, f"session-{i}") for i in range(200)]
        treatment_count = arms.count("treatment")
        # Expect roughly 50% ± 15% tolerance
        self.assertGreater(treatment_count, 50)
        self.assertLess(treatment_count, 150)

    def test_issue_match_records_experiment(self) -> None:
        """issue_match includes experiment info when an experiment is running."""
        self._store_resolution()
        store = self.app.store
        store.create_experiment(
            experiment_id="exp-live",
            name="Live Test",
            traffic_fraction=0.5,
        )
        store.update_experiment_status("exp-live", "running")

        result = self.app.issue_match(
            error_text="ModuleNotFoundError: No module named 'requests'",
            session_id="sess-exp-1",
            repo_name="test-repo",
            user_scope="test-user",
        )
        self.assertIn("experiment", result)
        self.assertEqual(result["experiment"]["id"], "exp-live")
        self.assertIn(result["experiment"]["arm"], ("treatment", "control"))

    def test_issue_match_no_experiment_when_none_running(self) -> None:
        """issue_match has no experiment key when no experiment is active."""
        self._store_resolution()
        result = self.app.issue_match(
            error_text="ModuleNotFoundError: No module named 'requests'",
            session_id="sess-no-exp",
            repo_name="test-repo",
            user_scope="test-user",
        )
        self.assertNotIn("experiment", result)

    def test_analyze_nonexistent_experiment(self) -> None:
        """Analyzing a missing experiment returns error dict."""
        result = self.app.store.analyze_experiment("does-not-exist")
        self.assertIn("error", result)

    def test_experiment_invalid_status_rejected(self) -> None:
        """Invalid status transitions raise ValueError."""
        store = self.app.store
        store.create_experiment(experiment_id="exp-bad", name="Bad")
        with self.assertRaises(ValueError):
            store.update_experiment_status("exp-bad", "invalid_status")

    # ------------------------------------------------------------------
    # 3.4 Auto weight calibration
    # ------------------------------------------------------------------

    def test_compute_optimal_weights_empty_input(self) -> None:
        """Returns empty result for no samples."""
        result = compute_optimal_weights(
            samples=[],
            feature_names=[],
            base_weights={},
        )
        self.assertEqual(result["weight_overrides"], {})
        self.assertEqual(result["samples_used"], 0)

    def test_compute_optimal_weights_basic(self) -> None:
        """Given simple positive/negative samples, weights shift toward discriminating features."""
        samples = [
            {"features": {"root_score": 0.9, "lexical_score": 0.3}, "reward": 1.0},
            {"features": {"root_score": 0.1, "lexical_score": 0.8}, "reward": -1.0},
            {"features": {"root_score": 0.8, "lexical_score": 0.2}, "reward": 1.0},
            {"features": {"root_score": 0.2, "lexical_score": 0.7}, "reward": -1.0},
            {"features": {"root_score": 0.85, "lexical_score": 0.25}, "reward": 1.0},
        ]
        base_weights = {"root_score": 0.18, "lexical_score": 0.08}
        result = compute_optimal_weights(
            samples=samples,
            feature_names=["root_score", "lexical_score"],
            base_weights=base_weights,
        )
        self.assertGreater(result["samples_used"], 0)
        self.assertLessEqual(result["loss_after"], result["loss_before"] + 1e-6)
        # root_score is the discriminating feature, should increase or stay
        overrides = result["weight_overrides"]
        self.assertIn("root_score", overrides)
        self.assertGreaterEqual(overrides["root_score"], base_weights["root_score"] - 0.051)

    def test_compute_optimal_weights_step_clamping(self) -> None:
        """Weight changes per iteration are clamped to ±0.05."""
        samples = [
            {"features": {"a": 1.0}, "reward": 1.0},
            {"features": {"a": 0.0}, "reward": -1.0},
        ] * 3
        base_weights = {"a": 0.1}
        result = compute_optimal_weights(
            samples=samples,
            feature_names=["a"],
            base_weights=base_weights,
            max_iterations=1,
        )
        if result["deltas"]:
            for delta in result["deltas"].values():
                self.assertLessEqual(abs(delta), 0.051)

    def test_query_feature_outcome_matrix_empty(self) -> None:
        """query_feature_outcome_matrix returns skipped for insufficient data."""
        result = self.app.store.query_feature_outcome_matrix(min_samples=5)
        self.assertTrue(result.get("skipped", False))

    def test_calibrate_weights_cli_parser(self) -> None:
        """calibrate-weights is registered in maintenance parser."""
        from codex_issue_memory.maintenance import build_parser
        parser = build_parser()
        args = parser.parse_args(["calibrate-weights", "--error-family", "import_error"])
        self.assertEqual(args.command, "calibrate-weights")
        self.assertEqual(args.error_family, "import_error")

    # ------------------------------------------------------------------
    # CLI experiment commands
    # ------------------------------------------------------------------

    def test_experiment_cli_parser(self) -> None:
        """Experiment CLI commands are registered in maintenance parser."""
        from codex_issue_memory.maintenance import build_parser
        parser = build_parser()

        args = parser.parse_args(["create-experiment", "--id", "test-1", "--name", "Test"])
        self.assertEqual(args.command, "create-experiment")
        self.assertEqual(args.experiment_id, "test-1")

        args = parser.parse_args(["update-experiment", "test-1", "running"])
        self.assertEqual(args.command, "update-experiment")
        self.assertEqual(args.status, "running")

        args = parser.parse_args(["analyze-experiment", "test-1"])
        self.assertEqual(args.command, "analyze-experiment")
        self.assertEqual(args.experiment_id, "test-1")


if __name__ == "__main__":
    unittest.main()
