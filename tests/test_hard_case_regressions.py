from __future__ import annotations

import os
import tempfile
from pathlib import Path
import unittest

from codex_issue_memory.app import IssueMemoryApp
from codex_issue_memory.benchmarks.dense_bandit import seed_dense_bandit_memory
from codex_issue_memory.benchmarks.user_domains import seed_user_domain_memory
from codex_issue_memory.normalization.classify import classify_from_text


class HardCaseClassificationRegressionTests(unittest.TestCase):
    def test_matlab_engine_import_symptom_prefers_import_error(self) -> None:
        family, root, _tags, _evidence = classify_from_text(
            "matlab engine cannot be imported while launching bellman hjb offline value iteration bridge from python"
        )
        self.assertEqual(family, "import_error")
        self.assertEqual(root, "missing_python_module")

    def test_yaml_key_not_found_prefers_config_error_over_path_error(self) -> None:
        family, root, _tags, _evidence = classify_from_text(
            "KeyError: rotor_3 not found in mixer yaml after vtol allocation refactor"
        )
        self.assertEqual(family, "config_error")
        self.assertEqual(root, "invalid_config_key")


class HardCaseMatchingRegressionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory(prefix="issue-memory-hard-case-")
        base = Path(self.temp_dir.name)
        os.environ["ISSUE_MEMORY_HOME"] = str(base / "share")
        os.environ["ISSUE_MEMORY_DB_PATH"] = str(
            base / "share" / "issue_memory.sqlite3"
        )
        os.environ["ISSUE_MEMORY_STATE_DIR"] = str(base / "state")
        os.environ["ISSUE_MEMORY_BACKUP_DIR"] = str(base / "share" / "backups")
        os.environ["ISSUE_MEMORY_LOG_DIR"] = str(base / "state" / "log")
        self.app = IssueMemoryApp()
        seed_user_domain_memory(self.app)
        seed_dense_bandit_memory(self.app)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_matlab_engine_hjb_noise_matches_import_case(self) -> None:
        result = self.app.issue_match(
            error_text="MATLAB engine cannot be imported while launching Bellman/HJB offline value iteration bridge from Python",
            file_path="control/matlab/bridge.py",
            command="python run_value_iteration.py",
            repo_name="optimal-control-suite",
            project_scope="global",
            limit=3,
        )
        self.assertIn(result["decision"]["status"], {"match", "ambiguous"})
        self.assertTrue(result["matches"])
        self.assertEqual(result["matches"][0]["title"], "MATLAB engine module missing")

    def test_vtol_yaml_noise_matches_yaml_key_case(self) -> None:
        result = self.app.issue_match(
            error_text="KeyError: rotor_3 not found in mixer yaml after vtol allocation refactor; json telemetry write is unrelated",
            file_path="uav/vtol/mixer_config.yaml",
            command="python run_vtol_mixer.py",
            repo_name="uav-lab",
            project_scope="global",
            limit=3,
        )
        self.assertIn(result["decision"]["status"], {"match", "ambiguous"})
        self.assertTrue(result["matches"])
        self.assertEqual(result["matches"][0]["title"], "VTOL mixer YAML key missing")


if __name__ == "__main__":
    unittest.main()
