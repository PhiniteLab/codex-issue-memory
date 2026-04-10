from __future__ import annotations

import os
import tempfile
from pathlib import Path
import unittest

from codex_issue_memory.app import IssueMemoryApp


class DecisionAndSearchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory(prefix="issue-memory-decision-")
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

    def test_close_candidates_marked_ambiguous(self) -> None:
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
            domain="python",
        )

        result = self.app.issue_match(
            error_text="ModuleNotFoundError: No module named requests",
            project_scope="global",
            limit=3,
        )

        self.assertEqual(result["decision"]["status"], "ambiguous")
        self.assertGreaterEqual(len(result["matches"]), 2)

    def test_issue_search_is_deterministic(self) -> None:
        first = self.app.issue_record_resolution(
            title="Relative sqlite path breaks outside repo root",
            raw_error="FileNotFoundError: references/contractsDatabase.sqlite3",
            canonical_fix="Resolve the db path relative to __file__ instead of cwd.",
            prevention_rule="No production DB path may depend on cwd.",
            project_scope="global",
            canonical_symptom="sqlite database path fails outside repo root",
            verification_steps="Run from repo root and external cwd.",
            tags="sqlite,path,cwd",
            error_family="sqlite_error",
            root_cause_class="cwd_relative_path_bug",
            command="python -m app.main",
            file_path="services/db_loader.py",
            domain="python",
        )
        self.app.issue_record_resolution(
            title="SQLite database locked during concurrent write",
            raw_error="sqlite3.OperationalError: database is locked",
            canonical_fix="Serialize writers or increase busy timeout for the connection.",
            prevention_rule="Coordinate concurrent writers against sqlite.",
            project_scope="global",
            canonical_symptom="concurrent sqlite writes trigger database locked",
            verification_steps="Run two overlapping writes and verify timeout behavior.",
            tags="sqlite,lock,concurrency",
            error_family="sqlite_error",
            root_cause_class="database_locked",
            domain="python",
        )
        self.app.issue_record_resolution(
            title="Pandas sheet header mismatch",
            raw_error="KeyError: missing column id while loading xlsx sheet",
            canonical_fix="Normalize worksheet headers before selecting required columns.",
            prevention_rule="Map spreadsheet headers to canonical names before parsing.",
            project_scope="global",
            canonical_symptom="xlsx header normalization missing for pandas loader",
            verification_steps="Load the worksheet and assert canonical headers exist.",
            tags="pandas,excel,header",
            error_family="excel_header_mapping_error",
            root_cause_class="excel_header_normalization_missing",
            domain="python",
        )

        result_a = self.app.issue_search(
            query="sqlite path cwd", project_scope="global", limit=3
        )
        result_b = self.app.issue_search(
            query="sqlite path cwd", project_scope="global", limit=3
        )

        ids_a = [row["pattern_id"] for row in result_a["patterns"]]
        ids_b = [row["pattern_id"] for row in result_b["patterns"]]
        self.assertEqual(ids_a, ids_b)
        self.assertTrue(ids_a)
        self.assertEqual(ids_a[0], first["pattern_id"])


if __name__ == "__main__":
    unittest.main()
