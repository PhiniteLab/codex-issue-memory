from __future__ import annotations

import os
import tempfile
from pathlib import Path
import unittest

from codex_issue_memory.app import IssueMemoryApp
from codex_issue_memory.benchmarks import seed_dense_bandit_memory


class StrategyBanditShadowModeTests(unittest.TestCase):
    _ENV_KEYS = (
        "ISSUE_MEMORY_HOME",
        "ISSUE_MEMORY_DB_PATH",
        "ISSUE_MEMORY_STATE_DIR",
        "ISSUE_MEMORY_BACKUP_DIR",
        "ISSUE_MEMORY_LOG_DIR",
        "ISSUE_MEMORY_ENABLE_DENSE_RETRIEVAL",
        "ISSUE_MEMORY_ENABLE_STRATEGY_BANDIT",
        "ISSUE_MEMORY_ENABLE_STRATEGY_BANDIT_SHADOW_MODE",
    )

    def setUp(self) -> None:
        self._env_backup = {key: os.environ.get(key) for key in self._ENV_KEYS}
        self.temp_dir = tempfile.TemporaryDirectory(prefix="issue-memory-shadow-bandit-")
        base = Path(self.temp_dir.name)
        os.environ["ISSUE_MEMORY_HOME"] = str(base / "share")
        os.environ["ISSUE_MEMORY_DB_PATH"] = str(base / "share" / "issue_memory.sqlite3")
        os.environ["ISSUE_MEMORY_STATE_DIR"] = str(base / "state")
        os.environ["ISSUE_MEMORY_BACKUP_DIR"] = str(base / "share" / "backups")
        os.environ["ISSUE_MEMORY_LOG_DIR"] = str(base / "state" / "log")
        os.environ["ISSUE_MEMORY_ENABLE_DENSE_RETRIEVAL"] = "1"
        os.environ["ISSUE_MEMORY_ENABLE_STRATEGY_BANDIT"] = "1"
        os.environ["ISSUE_MEMORY_ENABLE_STRATEGY_BANDIT_SHADOW_MODE"] = "1"
        self.app = IssueMemoryApp()
        seed_dense_bandit_memory(self.app)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()
        for key, value in self._env_backup.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def test_shadow_mode_marks_promoted_candidate_without_reordering(self) -> None:
        first = self.app.issue_match(
            error_text="ModuleNotFoundError: No module named requests",
            project_scope="global",
            repo_name="tooling-lab",
            limit=3,
        )
        self.assertEqual(first["decision"]["status"], "ambiguous")
        baseline_pattern_id = int(first["matches"][0]["pattern_id"])
        promoted_pattern_id = int(first["matches"][1]["pattern_id"])

        self.app.issue_feedback(
            retrieval_event_id=int(first["retrieval_event_id"]),
            feedback_type="fix_verified",
            candidate_rank=2,
            notes="Train second strategy in shadow mode.",
        )

        second = self.app.issue_match(
            error_text="ModuleNotFoundError: No module named requests",
            project_scope="global",
            repo_name="tooling-lab",
            limit=3,
        )
        self.assertEqual(int(second["matches"][0]["pattern_id"]), baseline_pattern_id)
        self.assertTrue(
            any("strategy-bandit-shadow-promote" in reason for match in second["matches"] for reason in match["why"])
        )
        self.assertIn(promoted_pattern_id, {int(match["pattern_id"]) for match in second["matches"]})


if __name__ == "__main__":
    unittest.main()
