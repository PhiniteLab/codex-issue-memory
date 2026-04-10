from __future__ import annotations

import os
import tempfile
from pathlib import Path
import unittest

from codex_issue_memory.app import IssueMemoryApp
from codex_issue_memory.benchmarks import (
    USER_DOMAIN_QUERY_CASES,
    run_user_domain_benchmark,
    seed_user_domain_memory,
)


class UserDomainBenchmarkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory(
            prefix="issue-memory-user-benchmark-"
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

    def test_benchmark_summary_reaches_expected_accuracy(self) -> None:
        report = run_user_domain_benchmark(self.app, repeats=2)
        self.assertGreaterEqual(report["top1_accuracy"], 0.95)
        self.assertEqual(report["failures"], [])
        self.assertEqual(report["query_count"], len(USER_DOMAIN_QUERY_CASES))
        self.assertGreater(report["latency_ms"]["mean"], 0.0)


for index, case in enumerate(USER_DOMAIN_QUERY_CASES, start=1):

    def _make_test(current_case=case):
        def _test(self: UserDomainBenchmarkTests) -> None:
            result = self.app.issue_match(
                error_text=current_case.error_text,
                file_path=current_case.file_path,
                command=current_case.command,
                repo_name=current_case.repo_name,
                project_scope=current_case.project_scope,
                limit=3,
            )
            self.assertTrue(result["matches"], msg=current_case.slug)
            self.assertEqual(
                result["matches"][0]["title"],
                current_case.expected_title,
                msg=current_case.slug,
            )

        return _test

    setattr(
        UserDomainBenchmarkTests,
        f"test_user_domain_case_{index:02d}_{case.slug}",
        _make_test(),
    )


if __name__ == "__main__":
    unittest.main()
