from __future__ import annotations

import os
import tempfile
from pathlib import Path
import unittest

from codex_issue_memory.app import IssueMemoryApp


class MatchingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory(prefix="issue-memory-test-")
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

    def test_sqlite_cwd_issue_matches(self) -> None:
        recorded = self.app.issue_record_resolution(
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
        self.assertIn(recorded["action"], {"created", "updated"})

        result = self.app.issue_match(
            error_text="FileNotFoundError: config/contractsDatabase.sqlite3",
            command="python -m app.main",
            file_path="services/db_loader.py",
            project_scope="global",
        )
        self.assertTrue(result["matches"])
        self.assertEqual(
            result["matches"][0]["root_cause_class"], "cwd_relative_path_bug"
        )

    def test_tensor_device_issue_matches(self) -> None:
        self.app.issue_record_resolution(
            title="PyTorch tensors on mixed devices",
            raw_error="RuntimeError: Expected all tensors to be on the same device, but found at least two devices, cuda:0 and cpu!",
            canonical_fix="Move model, inputs, and targets onto the same device before the forward pass.",
            prevention_rule="Assert device consistency before training and inference.",
            project_scope="global",
            canonical_symptom="mixed cuda and cpu tensors during forward pass",
            verification_steps="Run one forward pass on a sample batch and assert all tensors share one device.",
            tags="pytorch,device,cuda,cpu",
            error_family="tensor_device_error",
            root_cause_class="tensor_cross_device_mix",
            domain="pytorch",
        )
        result = self.app.issue_match(
            error_text="RuntimeError: Expected all tensors to be on the same device, but got cpu and cuda",
            project_scope="global",
        )
        self.assertTrue(result["matches"])
        self.assertEqual(result["matches"][0]["error_family"], "tensor_device_error")


if __name__ == "__main__":
    unittest.main()
