from __future__ import annotations

import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
import unittest
from unittest import mock

from codex_issue_memory.app import IssueMemoryApp
from codex_issue_memory.backup import BackupManager
from codex_issue_memory.services.consolidation_service import ConsolidationPlan


class Phase5OperationsTests(unittest.TestCase):
    _ENV_KEYS = (
        "ISSUE_MEMORY_HOME",
        "ISSUE_MEMORY_DB_PATH",
        "ISSUE_MEMORY_STATE_DIR",
        "ISSUE_MEMORY_BACKUP_DIR",
        "ISSUE_MEMORY_LOG_DIR",
        "ISSUE_MEMORY_ENABLE_DENSE_RETRIEVAL",
        "ISSUE_MEMORY_ENABLE_STRATEGY_BANDIT",
        "ISSUE_MEMORY_ENABLE_REDACTION",
    )

    def setUp(self) -> None:
        self._env_backup = {key: os.environ.get(key) for key in self._ENV_KEYS}
        self.temp_dir = tempfile.TemporaryDirectory(prefix="issue-memory-phase5-")
        base = Path(self.temp_dir.name)
        os.environ["ISSUE_MEMORY_HOME"] = str(base / "share")
        os.environ["ISSUE_MEMORY_DB_PATH"] = str(
            base / "share" / "issue_memory.sqlite3"
        )
        os.environ["ISSUE_MEMORY_STATE_DIR"] = str(base / "state")
        os.environ["ISSUE_MEMORY_BACKUP_DIR"] = str(base / "share" / "backups")
        os.environ["ISSUE_MEMORY_LOG_DIR"] = str(base / "state" / "log")
        os.environ["ISSUE_MEMORY_ENABLE_DENSE_RETRIEVAL"] = "0"
        os.environ["ISSUE_MEMORY_ENABLE_STRATEGY_BANDIT"] = "0"
        os.environ["ISSUE_MEMORY_ENABLE_REDACTION"] = "1"
        self.app = IssueMemoryApp()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()
        for key, value in self._env_backup.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def _store_basic_resolution(
        self, *, title: str = "Relative sqlite path breaks outside repo root"
    ) -> dict[str, Any]:
        return self.app.issue_record_resolution(
            title=title,
            raw_error="FileNotFoundError: references/contractsDatabase.sqlite3",
            canonical_fix="Resolve the SQLite path relative to __file__.",
            prevention_rule="No production DB path may depend on cwd.",
            project_scope="global",
            user_scope="mehmet",
            repo_name="repo-alpha",
            command="python -m app.main",
            file_path="services/db_loader.py",
            verification_output="Authorization: Bearer SECRET-TOKEN-1234567890\npassword=abc123",
            env_json='{"OPENAI_API_KEY":"sk-secret-test-123456789", "debug": "ok"}',
            resolution_notes="Keep password=abc123 out of persisted diagnostics.",
        )

    @staticmethod
    def _require_int(value: object) -> int:
        if not isinstance(value, int):
            raise AssertionError(f"Expected int identifier, got {type(value).__name__}")
        return value

    def test_redaction_and_metrics_summary(self) -> None:
        stored = self._store_basic_resolution()
        match = self.app.issue_match(
            error_text="FileNotFoundError: references/contractsDatabase.sqlite3 while running python -m app.main from another directory",
            command="python -m app.main",
            file_path="services/db_loader.py",
            repo_name="repo-alpha",
            user_scope="mehmet",
            project_scope="global",
            limit=3,
        )
        self.app.issue_feedback(
            retrieval_event_id=int(match["retrieval_event_id"]),
            feedback_type="fix_verified",
            candidate_rank=1,
            notes="Verified during metrics test.",
        )

        bundle = self.app.issue_get(
            pattern_id=self._require_int(stored["pattern_id"]),
            include_examples=True,
            examples_limit=5,
        )
        episode = bundle["episodes"][0]
        self.assertEqual(episode["env_json"]["OPENAI_API_KEY"], "[REDACTED]")
        self.assertIn("[REDACTED]", episode["verification_output"])
        self.assertNotIn("SECRET-TOKEN-1234567890", episode["verification_output"])
        self.assertIn("[REDACTED]", episode["resolution_notes"])

        metrics = self.app.issue_metrics(window_days=30)
        self.assertGreaterEqual(metrics["retrieval"]["total"], 1)
        self.assertEqual(metrics["feedback"]["counts"]["fix_verified"], 1)
        self.assertGreater(metrics["database"]["bytes"], 0)
        self.assertIn("safe_override_count", metrics["retrieval"])

    def test_review_queue_create_and_resolve(self) -> None:
        forced_plan = ConsolidationPlan(
            matched_pattern_id=None,
            matched_variant_id=None,
            pattern_signature="review-pattern-signature",
            proposed_pattern_key="review-pattern-signature",
            proposed_variant_key="review-variant-key",
            match_strategy="new_pattern",
            variant_strategy="new_variant_review",
            consolidation_score=0.71,
            reasons=["manual-review-required"],
            requires_review=True,
        )
        with mock.patch.object(
            self.app.record_service.consolidation_service,
            "plan",
            return_value=forced_plan,
        ):
            stored = self.app.issue_record_resolution(
                title="Needs review before activation",
                raw_error="ValueError: suspicious consolidation candidate",
                canonical_fix="Keep the safer split until reviewed.",
                prevention_rule="Review uncertain merges before activation.",
                project_scope="global",
                user_scope="mehmet",
                repo_name="review-lab",
            )

        queue = self.app.issue_review_queue(status="pending", limit=10)
        self.assertEqual(queue["count"], 1)
        item = queue["items"][0]
        self.assertEqual(item["status"], "pending")

        bundle_before = self.app.issue_get(
            pattern_id=self._require_int(stored["pattern_id"]),
            include_examples=True,
            examples_limit=5,
        )
        self.assertEqual(bundle_before["variants"][0]["status"], "provisional")
        self.assertEqual(bundle_before["episodes"][0]["consolidation_status"], "review")

        resolved = self.app.issue_review_resolve(
            review_id=int(item["id"]), decision="approve", note="Looks reusable."
        )
        self.assertTrue(resolved["found"])
        self.assertEqual(resolved["item"]["status"], "approved")

        bundle_after = self.app.issue_get(
            pattern_id=self._require_int(stored["pattern_id"]),
            include_examples=True,
            examples_limit=5,
        )
        self.assertEqual(bundle_after["variants"][0]["status"], "active")
        self.assertEqual(
            bundle_after["episodes"][0]["consolidation_status"], "attached"
        )

    def test_backup_restore_rewinds_database(self) -> None:
        first = self._store_basic_resolution(title="First restorable issue")
        manager = BackupManager.from_env()
        backup = manager.create_backup()
        verified = manager.verify_backup(backup.local_path)
        self.assertTrue(verified["verified"])

        second = self.app.issue_record_resolution(
            title="Second issue after backup",
            raw_error="ModuleNotFoundError: No module named requests",
            canonical_fix="Install requests into the active environment.",
            prevention_rule="Pin runtime dependencies.",
            project_scope="global",
        )
        recent_before = self.app.issue_recent(limit=10)
        self.assertGreaterEqual(len(recent_before["patterns"]), 2)

        restored = manager.restore_backup(backup.local_path, create_safety_backup=False)
        self.assertEqual(restored["status"], "ok")

        app_after = IssueMemoryApp()
        self.assertTrue(
            app_after.issue_get(pattern_id=self._require_int(first["pattern_id"]))[
                "found"
            ]
        )
        self.assertFalse(
            app_after.issue_get(pattern_id=self._require_int(second["pattern_id"]))[
                "found"
            ]
        )

    def test_prune_operational_data_deletes_old_telemetry_and_resolved_reviews(
        self,
    ) -> None:
        forced_plan = ConsolidationPlan(
            matched_pattern_id=None,
            matched_variant_id=None,
            pattern_signature="prune-review-pattern",
            proposed_pattern_key="prune-review-pattern",
            proposed_variant_key="prune-review-variant",
            match_strategy="new_pattern",
            variant_strategy="new_variant_review",
            consolidation_score=0.69,
            reasons=["manual-review-required"],
            requires_review=True,
        )
        with mock.patch.object(
            self.app.record_service.consolidation_service,
            "plan",
            return_value=forced_plan,
        ):
            stored = self.app.issue_record_resolution(
                title="Retention review issue",
                raw_error="ValueError: retention review candidate",
                canonical_fix="Keep as provisional until approved.",
                prevention_rule="Resolve review queue promptly.",
                project_scope="global",
                user_scope="mehmet",
                repo_name="retention-lab",
            )
        queue = self.app.issue_review_queue(status="pending", limit=5)
        review_id = int(queue["items"][0]["id"])
        self.app.issue_review_resolve(
            review_id=review_id, decision="approve", note="done"
        )

        match = self.app.issue_match(
            error_text="ValueError: retention review candidate",
            project_scope="global",
            user_scope="mehmet",
            repo_name="retention-lab",
            limit=3,
        )
        self.assertIsNotNone(match["retrieval_event_id"])

        old_timestamp = (
            (datetime.now(timezone.utc) - timedelta(days=400))
            .replace(microsecond=0)
            .isoformat()
        )
        with self.app.store.managed_connection(immediate=True) as conn:
            conn.execute("UPDATE retrieval_events SET created_at = ?", (old_timestamp,))
            conn.execute(
                "UPDATE retrieval_candidates SET created_at = ?", (old_timestamp,)
            )
            conn.execute(
                "UPDATE review_queue SET resolved_at = ?, created_at = ? WHERE id = ?",
                (old_timestamp, old_timestamp, review_id),
            )

        pruned = self.app.store.prune_operational_data(
            telemetry_retention_days=30, resolved_review_retention_days=30
        )
        self.assertGreaterEqual(pruned["retrieval_events_deleted"], 1)
        self.assertGreaterEqual(pruned["resolved_reviews_deleted"], 1)

        queue_after = self.app.issue_review_queue(status="approved", limit=5)
        self.assertEqual(queue_after["count"], 0)
        bundle = self.app.issue_get(
            pattern_id=int(stored["pattern_id"]),
            include_examples=True,
            examples_limit=5,
        )
        self.assertTrue(bundle["found"])


if __name__ == "__main__":
    unittest.main()
