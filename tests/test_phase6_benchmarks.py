from __future__ import annotations

import os
import tempfile
from pathlib import Path
import unittest

from codex_issue_memory.app import IssueMemoryApp
from codex_issue_memory.benchmarks import run_hard_negative_benchmark, run_merge_correctness_stress, seed_hard_negative_memory


class Phase6BenchmarksTests(unittest.TestCase):
    _ENV_KEYS = (
        "ISSUE_MEMORY_HOME",
        "ISSUE_MEMORY_DB_PATH",
        "ISSUE_MEMORY_STATE_DIR",
        "ISSUE_MEMORY_BACKUP_DIR",
        "ISSUE_MEMORY_LOG_DIR",
    )

    def setUp(self) -> None:
        self._env_backup = {key: os.environ.get(key) for key in self._ENV_KEYS}
        self.temp_dir = tempfile.TemporaryDirectory(prefix="issue-memory-phase6-benchmarks-")
        base = Path(self.temp_dir.name)
        os.environ["ISSUE_MEMORY_HOME"] = str(base / "share")
        os.environ["ISSUE_MEMORY_DB_PATH"] = str(base / "share" / "issue_memory.sqlite3")
        os.environ["ISSUE_MEMORY_STATE_DIR"] = str(base / "state")
        os.environ["ISSUE_MEMORY_BACKUP_DIR"] = str(base / "share" / "backups")
        os.environ["ISSUE_MEMORY_LOG_DIR"] = str(base / "state" / "log")

    def tearDown(self) -> None:
        self.temp_dir.cleanup()
        for key, value in self._env_backup.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def test_hard_negative_benchmark_reports_safe_behavior(self) -> None:
        app = IssueMemoryApp()
        seed_hard_negative_memory(app)
        report = run_hard_negative_benchmark(app)
        self.assertGreaterEqual(report["positive_top1_accuracy"], 0.6)
        self.assertLessEqual(report["unsafe_clear_match_rate"], 0.5)

    def test_merge_correctness_stress_avoids_catastrophic_variant_merge(self) -> None:
        app = IssueMemoryApp()
        report = run_merge_correctness_stress(app)
        self.assertEqual(report["catastrophic_variant_merge_count"], 0)
        self.assertGreaterEqual(report["success_rate"], 0.75)


if __name__ == "__main__":
    unittest.main()
