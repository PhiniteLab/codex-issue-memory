from __future__ import annotations

import os
import tempfile
from pathlib import Path
import unittest

from codex_issue_memory.app import IssueMemoryApp
from codex_issue_memory.benchmarks import seed_dense_bandit_memory


class PreferenceGuardrailTests(unittest.TestCase):
    _ENV_KEYS = (
        "ISSUE_MEMORY_HOME",
        "ISSUE_MEMORY_DB_PATH",
        "ISSUE_MEMORY_STATE_DIR",
        "ISSUE_MEMORY_BACKUP_DIR",
        "ISSUE_MEMORY_LOG_DIR",
        "ISSUE_MEMORY_ENABLE_DENSE_RETRIEVAL",
        "ISSUE_MEMORY_ENABLE_STRATEGY_BANDIT",
        "ISSUE_MEMORY_ENABLE_PREFERENCE_RULES",
    )

    def setUp(self) -> None:
        self._env_backup = {key: os.environ.get(key) for key in self._ENV_KEYS}
        self.temp_dir = tempfile.TemporaryDirectory(prefix="issue-memory-preferences-")
        base = Path(self.temp_dir.name)
        os.environ["ISSUE_MEMORY_HOME"] = str(base / "share")
        os.environ["ISSUE_MEMORY_DB_PATH"] = str(
            base / "share" / "issue_memory.sqlite3"
        )
        os.environ["ISSUE_MEMORY_STATE_DIR"] = str(base / "state")
        os.environ["ISSUE_MEMORY_BACKUP_DIR"] = str(base / "share" / "backups")
        os.environ["ISSUE_MEMORY_LOG_DIR"] = str(base / "state" / "log")
        os.environ["ISSUE_MEMORY_ENABLE_DENSE_RETRIEVAL"] = "1"
        os.environ["ISSUE_MEMORY_ENABLE_STRATEGY_BANDIT"] = "0"
        os.environ["ISSUE_MEMORY_ENABLE_PREFERENCE_RULES"] = "1"
        self.app = IssueMemoryApp()
        seed_dense_bandit_memory(self.app)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()
        for key, value in self._env_backup.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def test_issue_set_preference_promotes_matching_strategy(self) -> None:
        stored = self.app.issue_set_preference(
            instruction="Import hatalarında önce doğru virtualenv interpreter seçimini düzelt.",
            project_scope="global",
            user_scope="mehmet",
            repo_name="tooling-lab",
            mode="prefer",
        )
        self.assertTrue(stored["created"])
        self.assertEqual(stored["derived"]["strategy_key"], "fix_interpreter_or_venv")
        self.assertEqual(stored["derived"]["error_family"], "import_error")

        result = self.app.issue_match(
            error_text="ModuleNotFoundError: No module named requests",
            project_scope="global",
            repo_name="tooling-lab",
            user_scope="mehmet",
            limit=3,
        )
        self.assertTrue(result["matches"])
        self.assertEqual(
            result["matches"][0]["title"],
            "Requests missing because CLI uses wrong interpreter",
        )
        self.assertTrue(
            any(
                reason.startswith("preference-rule:fix_interpreter_or_venv")
                for reason in result["matches"][0]["why"]
            )
        )

        listed = self.app.issue_list_preferences(
            scope_type="user", scope_key="mehmet", limit=5
        )
        self.assertEqual(len(listed["rules"]), 1)
        self.assertEqual(listed["rules"][0]["strategy_key"], "fix_interpreter_or_venv")

    def test_issue_guardrails_surfaces_prevention_rules_and_preferences(self) -> None:
        self.app.issue_set_preference(
            instruction="Path sorunlarında önce __file__ bazlı çözümü uygula ve cwd bağımlılığını kaldır.",
            project_scope="global",
            user_scope="mehmet",
            repo_name="optimal-control-suite",
            mode="prefer",
        )
        result = self.app.issue_guardrails(
            command="python solve_hjb.py --grid data/hjb/value_grid.mat",
            file_path="control/hjb/load_value_grid.py",
            repo_name="optimal-control-suite",
            project_scope="global",
            user_scope="mehmet",
            limit=3,
        )
        self.assertTrue(result["guardrails"])
        self.assertTrue(
            any(
                "cwd" in item["prevention_rule"].lower()
                or "repository root" in item["prevention_rule"].lower()
                for item in result["guardrails"]
            )
        )
        self.assertTrue(result["preferences"]["preferred_strategies"])
        self.assertEqual(
            result["preferences"]["preferred_strategies"][0]["strategy_key"],
            "resolve_from___file__",
        )

    def test_issue_guardrails_filters_unrelated_sqlite_path_guardrails_for_import_queries(
        self,
    ) -> None:
        result = self.app.issue_guardrails(
            error_text="ModuleNotFoundError: No module named requests",
            repo_name="tooling-lab",
            project_scope="global",
            limit=5,
        )

        self.assertTrue(result["guardrails"])
        self.assertTrue(
            any("wrong interpreter" in item["title"].lower() for item in result["guardrails"])
        )
        self.assertFalse(
            any(
                "cwd" in item["prevention_rule"].lower()
                or "sqlite" in item["title"].lower()
                or "repository root" in item["prevention_rule"].lower()
                for item in result["guardrails"]
            )
        )


if __name__ == "__main__":
    unittest.main()
