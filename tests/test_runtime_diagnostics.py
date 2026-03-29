from __future__ import annotations

import os
import tempfile
from pathlib import Path
import unittest

from codex_issue_memory.app import IssueMemoryApp
from codex_issue_memory.benchmarks import run_runtime_diagnostics


class RuntimeDiagnosticsTests(unittest.TestCase):
    def test_runtime_diagnostics_integrity_and_rerank(self) -> None:
        with tempfile.TemporaryDirectory(prefix="issue-memory-runtime-diagnostics-test-") as temp_dir:
            base = Path(temp_dir)
            os.environ["ISSUE_MEMORY_HOME"] = str(base / "share")
            os.environ["ISSUE_MEMORY_DB_PATH"] = str(base / "share" / "issue_memory.sqlite3")
            os.environ["ISSUE_MEMORY_STATE_DIR"] = str(base / "state")
            os.environ["ISSUE_MEMORY_BACKUP_DIR"] = str(base / "share" / "backups")
            os.environ["ISSUE_MEMORY_LOG_DIR"] = str(base / "state" / "log")
            app = IssueMemoryApp()
            report = run_runtime_diagnostics(app, repeats=1)
            self.assertEqual(report["taxonomy"]["positive_failures"], [])
            self.assertEqual(report["taxonomy"]["negative_failures"], [])
            self.assertTrue(report["session_feedback_rerank_ok"])
            self.assertEqual(report["integrity_check"], "ok")
            self.assertEqual(report["consolidation"]["distinct_pattern_ids"], 1)
            self.assertEqual(report["consolidation"]["distinct_variant_ids"], 1)


if __name__ == "__main__":
    unittest.main()
