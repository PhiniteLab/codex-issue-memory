from __future__ import annotations

import os
import tempfile
from pathlib import Path
import unittest

from codex_issue_memory.app import IssueMemoryApp
from codex_issue_memory.benchmarks import (
    NEGATIVE_ABSTAIN_CASES,
    run_failure_taxonomy_benchmark,
    seed_user_domain_memory,
)


class FailureTaxonomyBenchmarkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory(
            prefix="issue-memory-failure-taxonomy-"
        )
        base = Path(cls.temp_dir.name)
        os.environ["ISSUE_MEMORY_HOME"] = str(base / "share")
        os.environ["ISSUE_MEMORY_DB_PATH"] = str(
            base / "share" / "issue_memory.sqlite3"
        )
        os.environ["ISSUE_MEMORY_STATE_DIR"] = str(base / "state")
        os.environ["ISSUE_MEMORY_BACKUP_DIR"] = str(base / "share" / "backups")
        os.environ["ISSUE_MEMORY_LOG_DIR"] = str(base / "state" / "log")
        cls.app = IssueMemoryApp()
        seed_user_domain_memory(cls.app)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temp_dir.cleanup()

    def test_failure_taxonomy_benchmark_summary(self) -> None:
        report = run_failure_taxonomy_benchmark(self.app, repeats=2)
        self.assertGreaterEqual(report["positive_top1_accuracy"], 0.99)
        self.assertGreaterEqual(report["positive_actionable_rate"], 0.99)
        self.assertEqual(report["negative_abstain_rate"], 1.0)
        self.assertEqual(report["positive_failures"], [])
        self.assertEqual(report["negative_failures"], [])
        self.assertGreater(report["latency_ms"]["overall"]["mean"], 0.0)


for index, case in enumerate(NEGATIVE_ABSTAIN_CASES, start=1):

    def _make_test(current_case=case):
        def _test(self: FailureTaxonomyBenchmarkTests) -> None:
            result = self.app.issue_match(
                error_text=current_case.error_text,
                file_path=current_case.file_path,
                command=current_case.command,
                repo_name=current_case.repo_name,
                project_scope=current_case.project_scope,
                limit=3,
            )
            self.assertEqual(
                result["decision"]["status"], "abstain", msg=current_case.slug
            )
            self.assertEqual(result["matches"], [], msg=current_case.slug)

        return _test

    setattr(
        FailureTaxonomyBenchmarkTests,
        f"test_negative_case_{index:02d}_{case.slug.replace('-', '_')}",
        _make_test(),
    )


if __name__ == "__main__":
    unittest.main()
