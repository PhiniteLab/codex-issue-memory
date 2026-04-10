from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any
import unittest

from codex_issue_memory.app import IssueMemoryApp


class Phase5OperationalMaturityTests(unittest.TestCase):
    """Tests for Phase 5 items: strategy metrics, latency tracking,
    degradation alarm, and batch learning safety gate."""

    _ENV_KEYS = (
        "ISSUE_MEMORY_HOME",
        "ISSUE_MEMORY_DB_PATH",
        "ISSUE_MEMORY_STATE_DIR",
        "ISSUE_MEMORY_BACKUP_DIR",
        "ISSUE_MEMORY_LOG_DIR",
        "ISSUE_MEMORY_ENABLE_DENSE_RETRIEVAL",
        "ISSUE_MEMORY_ENABLE_STRATEGY_BANDIT",
        "ISSUE_MEMORY_ENABLE_REDACTION",
        "ISSUE_MEMORY_FP_RATE_ALARM_THRESHOLD",
        "ISSUE_MEMORY_FEEDBACK_BATCH_WINDOW_SECONDS",
        "ISSUE_MEMORY_FEEDBACK_BATCH_FP_REVIEW_THRESHOLD",
    )

    def setUp(self) -> None:
        self._env_backup = {key: os.environ.get(key) for key in self._ENV_KEYS}
        self.temp_dir = tempfile.TemporaryDirectory(prefix="issue-memory-p5om-")
        base = Path(self.temp_dir.name)
        os.environ["ISSUE_MEMORY_HOME"] = str(base / "share")
        os.environ["ISSUE_MEMORY_DB_PATH"] = str(base / "share" / "issue_memory.sqlite3")
        os.environ["ISSUE_MEMORY_STATE_DIR"] = str(base / "state")
        os.environ["ISSUE_MEMORY_BACKUP_DIR"] = str(base / "share" / "backups")
        os.environ["ISSUE_MEMORY_LOG_DIR"] = str(base / "state" / "log")
        os.environ["ISSUE_MEMORY_ENABLE_DENSE_RETRIEVAL"] = "0"
        os.environ["ISSUE_MEMORY_ENABLE_STRATEGY_BANDIT"] = "0"
        os.environ["ISSUE_MEMORY_ENABLE_REDACTION"] = "0"
        os.environ["ISSUE_MEMORY_FP_RATE_ALARM_THRESHOLD"] = "0.15"
        os.environ["ISSUE_MEMORY_FEEDBACK_BATCH_WINDOW_SECONDS"] = "300"
        os.environ["ISSUE_MEMORY_FEEDBACK_BATCH_FP_REVIEW_THRESHOLD"] = "3"
        self.app = IssueMemoryApp()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()
        for key, value in self._env_backup.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    # ── helpers ─────────────────────────────────────────────────────

    def _store_resolution(self, title: str = "SQLite path breaks") -> dict[str, Any]:
        return self.app.issue_record_resolution(
            title=title,
            raw_error="FileNotFoundError: db.sqlite3",
            canonical_fix="Use absolute path.",
            prevention_rule="Never rely on cwd for DB path.",
            project_scope="global",
            user_scope="test-user",
            repo_name="repo-a",
            command="python main.py",
            file_path="loader.py",
        )

    def _match_and_get_event(self, error_text: str = "FileNotFoundError: db.sqlite3") -> dict[str, Any]:
        return self.app.issue_match(
            error_text=error_text,
            command="python main.py",
            file_path="loader.py",
            repo_name="repo-a",
            user_scope="test-user",
            project_scope="global",
            limit=3,
        )

    # ── 5.1 Strategy-level metrics ─────────────────────────────────

    def test_metrics_summary_contains_strategy_section(self) -> None:
        self._store_resolution()
        match = self._match_and_get_event()
        self.app.issue_feedback(
            retrieval_event_id=int(match["retrieval_event_id"]),
            feedback_type="fix_verified",
            candidate_rank=1,
        )
        metrics = self.app.issue_metrics(window_days=30)
        self.assertIn("strategy", metrics)
        self.assertIsInstance(metrics["strategy"], dict)

    # ── 5.2 Latency tracking ──────────────────────────────────────

    def test_metrics_summary_contains_stage_latency(self) -> None:
        self._store_resolution()
        self._match_and_get_event()
        metrics = self.app.issue_metrics(window_days=30)
        retrieval_section = metrics["retrieval"]
        self.assertIn("stage_latency_ms", retrieval_section)
        latency = retrieval_section["stage_latency_ms"]
        for key in ("retrieval", "ranking", "decision"):
            self.assertIn(key, latency)
            self.assertIn("p50", latency[key])
            self.assertIn("p95", latency[key])

    def test_stage_latency_columns_populated(self) -> None:
        self._store_resolution()
        self._match_and_get_event()
        store = self.app.store
        with store.managed_connection() as conn:
            row = conn.execute(
                "SELECT retrieval_latency_ms, ranking_latency_ms, decision_latency_ms FROM retrieval_events LIMIT 1"
            ).fetchone()
        self.assertIsNotNone(row)
        for col in ("retrieval_latency_ms", "ranking_latency_ms", "decision_latency_ms"):
            self.assertIsInstance(row[col], int)

    # ── 5.3 Degradation alarm ─────────────────────────────────────

    def test_fp_alarm_false_when_no_fp(self) -> None:
        self._store_resolution()
        match = self._match_and_get_event()
        self.app.issue_feedback(
            retrieval_event_id=int(match["retrieval_event_id"]),
            feedback_type="fix_verified",
            candidate_rank=1,
        )
        metrics = self.app.issue_metrics(window_days=30)
        self.assertIn("fp_alarm", metrics["feedback"])
        self.assertFalse(metrics["feedback"]["fp_alarm"])

    def test_fp_alarm_true_when_high_fp_rate(self) -> None:
        """Produce enough false_positive feedback to cross the alarm threshold."""
        os.environ["ISSUE_MEMORY_FP_RATE_ALARM_THRESHOLD"] = "0.01"
        os.environ["ISSUE_MEMORY_FEEDBACK_BATCH_WINDOW_SECONDS"] = "0"
        self.app = IssueMemoryApp()

        self._store_resolution()
        for _ in range(3):
            match = self._match_and_get_event()
            self.app.issue_feedback(
                retrieval_event_id=int(match["retrieval_event_id"]),
                feedback_type="false_positive",
                candidate_rank=1,
            )
        metrics = self.app.issue_metrics(window_days=30)
        self.assertTrue(metrics["feedback"]["fp_alarm"])

    # ── 5.4 Batch learning safety gate ─────────────────────────────

    def test_batch_gate_triggers_after_threshold(self) -> None:
        """After ≥3 FPs in the window the next FP is batched, not applied."""
        os.environ["ISSUE_MEMORY_FEEDBACK_BATCH_FP_REVIEW_THRESHOLD"] = "3"
        os.environ["ISSUE_MEMORY_FEEDBACK_BATCH_WINDOW_SECONDS"] = "600"
        self.app = IssueMemoryApp()

        self._store_resolution()
        # Submit first 3 FPs — should be applied immediately
        for i in range(3):
            match = self._match_and_get_event()
            result = self.app.issue_feedback(
                retrieval_event_id=int(match["retrieval_event_id"]),
                feedback_type="false_positive",
                candidate_rank=1,
                notes=f"fp-{i}",
            )
            self.assertEqual(result["status"], "ok", f"FP #{i} should be applied normally")

        # 4th FP should be batched
        match = self._match_and_get_event()
        result = self.app.issue_feedback(
            retrieval_event_id=int(match["retrieval_event_id"]),
            feedback_type="false_positive",
            candidate_rank=1,
            notes="fp-gated",
        )
        self.assertEqual(result["status"], "batched")
        self.assertIn("batch_gate", result)

    def test_batch_gate_skipped_when_window_zero(self) -> None:
        """batch_window_seconds=0 disables the gate entirely."""
        os.environ["ISSUE_MEMORY_FEEDBACK_BATCH_WINDOW_SECONDS"] = "0"
        os.environ["ISSUE_MEMORY_FEEDBACK_BATCH_FP_REVIEW_THRESHOLD"] = "2"
        self.app = IssueMemoryApp()

        self._store_resolution()
        for i in range(4):
            match = self._match_and_get_event()
            result = self.app.issue_feedback(
                retrieval_event_id=int(match["retrieval_event_id"]),
                feedback_type="false_positive",
                candidate_rank=1,
            )
            self.assertEqual(result["status"], "ok", "All FPs should pass when window=0")

    def test_flush_feedback_batch(self) -> None:
        """Enqueued items can be flushed (applied)."""
        os.environ["ISSUE_MEMORY_FEEDBACK_BATCH_FP_REVIEW_THRESHOLD"] = "2"
        os.environ["ISSUE_MEMORY_FEEDBACK_BATCH_WINDOW_SECONDS"] = "600"
        self.app = IssueMemoryApp()

        self._store_resolution()
        # Trigger 2 applied + 1 batched
        for _ in range(2):
            match = self._match_and_get_event()
            self.app.issue_feedback(
                retrieval_event_id=int(match["retrieval_event_id"]),
                feedback_type="false_positive",
                candidate_rank=1,
            )
        match = self._match_and_get_event()
        batched = self.app.issue_feedback(
            retrieval_event_id=int(match["retrieval_event_id"]),
            feedback_type="false_positive",
            candidate_rank=1,
        )
        self.assertEqual(batched["status"], "batched")

        flushed = self.app.store.flush_feedback_batch()
        self.assertEqual(len(flushed), 1)
        self.assertEqual(flushed[0]["feedback_type"], "false_positive")

    def test_non_fp_feedback_not_gated(self) -> None:
        """Only false_positive feedback triggers the batch gate."""
        os.environ["ISSUE_MEMORY_FEEDBACK_BATCH_FP_REVIEW_THRESHOLD"] = "2"
        os.environ["ISSUE_MEMORY_FEEDBACK_BATCH_WINDOW_SECONDS"] = "600"
        self.app = IssueMemoryApp()

        self._store_resolution()
        for _ in range(5):
            match = self._match_and_get_event()
            result = self.app.issue_feedback(
                retrieval_event_id=int(match["retrieval_event_id"]),
                feedback_type="fix_verified",
                candidate_rank=1,
            )
            self.assertEqual(result["status"], "ok")

    # ── Migration ──────────────────────────────────────────────────

    def test_feedback_batch_queue_table_exists(self) -> None:
        store = self.app.store
        with store.managed_connection() as conn:
            tables = {
                row["name"]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
        self.assertIn("feedback_batch_queue", tables)


if __name__ == "__main__":
    unittest.main()
