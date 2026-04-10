from __future__ import annotations

import os
import tempfile
from pathlib import Path
import unittest

from codex_issue_memory.app import IssueMemoryApp


class SessionMemoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory(prefix="issue-memory-session-")
        base = Path(self.temp_dir.name)
        os.environ["ISSUE_MEMORY_HOME"] = str(base / "share")
        os.environ["ISSUE_MEMORY_DB_PATH"] = str(
            base / "share" / "issue_memory.sqlite3"
        )
        os.environ["ISSUE_MEMORY_STATE_DIR"] = str(base / "state")
        os.environ["ISSUE_MEMORY_BACKUP_DIR"] = str(base / "share" / "backups")
        os.environ["ISSUE_MEMORY_LOG_DIR"] = str(base / "state" / "log")
        self.app = IssueMemoryApp()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _seed_close_import_patterns(self) -> None:
        self.app.issue_record_resolution(
            title="Requests missing in API worker",
            raw_error="ModuleNotFoundError: No module named requests while starting API worker",
            canonical_fix="Install requests into the active environment used by the API worker.",
            prevention_rule="Pin and install runtime dependencies in the worker environment.",
            project_scope="global",
            canonical_symptom="requests import fails during api worker startup",
            verification_steps="Run the API worker import check inside the same environment.",
            tags="python,import,requests,api",
            error_family="import_error",
            root_cause_class="missing_python_module",
            command="python worker.py",
            file_path="api/worker.py",
            domain="python",
        )
        self.app.issue_record_resolution(
            title="Requests missing in CLI utility",
            raw_error="ImportError: cannot import name requests from CLI utility bootstrap",
            canonical_fix="Install requests into the environment used by the CLI entrypoint.",
            prevention_rule="Keep CLI dependencies synchronized with runtime requirements.",
            project_scope="global",
            canonical_symptom="requests import fails during cli bootstrap",
            verification_steps="Run the CLI import check inside the same environment.",
            tags="python,import,requests,cli",
            error_family="import_error",
            root_cause_class="missing_python_module",
            command="python cli.py",
            file_path="cli/bootstrap.py",
            domain="python",
        )

    def test_rejected_top_candidate_is_demoted_within_same_session(self) -> None:
        self._seed_close_import_patterns()
        query = "ModuleNotFoundError: No module named requests"

        first = self.app.issue_match(
            error_text=query,
            project_scope="global",
            session_id="session-rerank",
            limit=3,
        )
        self.assertEqual(first["decision"]["status"], "ambiguous")
        self.assertEqual(first["matches"][0]["pattern_id"], 1)

        feedback = self.app.issue_feedback(
            retrieval_event_id=first["retrieval_event_id"],
            feedback_type="candidate_rejected",
            candidate_rank=1,
            notes="API worker suggestion was wrong for this session.",
        )
        self.assertEqual(feedback["resolved_candidate"]["pattern_id"], 1)

        second = self.app.issue_match(
            error_text=query,
            project_scope="global",
            session_id="session-rerank",
            limit=3,
        )
        self.assertTrue(second["matches"])
        self.assertEqual(second["matches"][0]["pattern_id"], 2)
        self.assertEqual(second["matches"][1]["pattern_id"], 1)
        self.assertIn("session-rejection-memory", second["matches"][1]["why"])

        snapshot = self.app.session_service.snapshot("session-rerank")
        self.assertEqual(len(snapshot), 1)
        self.assertEqual(snapshot[0]["memory_key"], "rejected_variant:1:1")
        self.assertAlmostEqual(float(snapshot[0]["salience"]), 0.9, places=3)

    def test_positive_feedback_clears_rejection_memory_for_same_pattern(self) -> None:
        self._seed_close_import_patterns()
        query = "ModuleNotFoundError: No module named requests"

        first = self.app.issue_match(
            error_text=query,
            project_scope="global",
            session_id="session-clear",
            limit=3,
        )
        self.app.issue_feedback(
            retrieval_event_id=first["retrieval_event_id"],
            feedback_type="candidate_rejected",
            candidate_rank=1,
            notes="Reject once before later verification.",
        )
        snapshot = self.app.session_service.snapshot("session-clear")
        self.assertEqual(len(snapshot), 1)
        self.assertEqual(snapshot[0]["memory_key"], "rejected_variant:1:1")

        self.app.issue_feedback(
            retrieval_event_id=first["retrieval_event_id"],
            feedback_type="fix_verified",
            candidate_rank=1,
            notes="Same pattern later verified after more context.",
        )
        snapshot_after = self.app.session_service.snapshot("session-clear")
        keys = {row["memory_key"] for row in snapshot_after}
        self.assertNotIn("rejected_variant:1:1", keys)
        self.assertIn("accepted_variant:1:1", keys)


if __name__ == "__main__":
    unittest.main()
