from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
import unittest

from codex_issue_memory.app import IssueMemoryApp
from codex_issue_memory.benchmarks import (
    run_threshold_calibration,
    seed_real_world_memory,
)
from codex_issue_memory.maintenance import cmd_export_dashboard


class CalibrationAndDashboardTests(unittest.TestCase):
    _ENV_KEYS = (
        "ISSUE_MEMORY_HOME",
        "ISSUE_MEMORY_DB_PATH",
        "ISSUE_MEMORY_STATE_DIR",
        "ISSUE_MEMORY_BACKUP_DIR",
        "ISSUE_MEMORY_LOG_DIR",
        "ISSUE_MEMORY_ENABLE_CALIBRATION_PROFILE",
        "ISSUE_MEMORY_CALIBRATION_PROFILE_PATH",
    )

    def setUp(self) -> None:
        self._env_backup = {key: os.environ.get(key) for key in self._ENV_KEYS}
        self.temp_dir = tempfile.TemporaryDirectory(prefix="issue-memory-calibration-")
        base = Path(self.temp_dir.name)
        os.environ["ISSUE_MEMORY_HOME"] = str(base / "share")
        os.environ["ISSUE_MEMORY_DB_PATH"] = str(
            base / "share" / "issue_memory.sqlite3"
        )
        os.environ["ISSUE_MEMORY_STATE_DIR"] = str(base / "state")
        os.environ["ISSUE_MEMORY_BACKUP_DIR"] = str(base / "share" / "backups")
        os.environ["ISSUE_MEMORY_LOG_DIR"] = str(base / "state" / "log")
        os.environ["ISSUE_MEMORY_ENABLE_CALIBRATION_PROFILE"] = "1"
        os.environ["ISSUE_MEMORY_CALIBRATION_PROFILE_PATH"] = str(
            base / "state" / "calibration_profile.json"
        )
        self.app = IssueMemoryApp()
        seed_real_world_memory(self.app)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()
        for key, value in self._env_backup.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def test_calibration_profile_can_be_written_and_loaded(self) -> None:
        report = run_threshold_calibration(self.app)
        self.assertIn("global", report)
        self.assertIn("families", report)
        self.assertGreaterEqual(
            report["metrics"]["global"]["negative_safety_rate"], 0.5
        )

        profile_path = Path(os.environ["ISSUE_MEMORY_CALIBRATION_PROFILE_PATH"])
        profile_path.write_text(
            json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        reloaded = IssueMemoryApp()
        self.assertTrue(reloaded.matcher.calibration_profile)
        self.assertEqual(
            float(reloaded.matcher.calibration_profile["global"]["accept_threshold"]),
            float(report["global"]["accept_threshold"]),
        )

    def test_dashboard_export_writes_json_payload(self) -> None:
        self.app.store.save_report(
            "benchmark_real_world_eval",
            {"dataset": "real_world_eval", "top1_accuracy": 1.0},
        )
        self.app.store.save_report(
            "threshold_calibration", {"version": 1, "global": {"accept_threshold": 0.7}}
        )
        output = Path(self.temp_dir.name) / "dashboard.json"
        cmd_export_dashboard(str(output), "json", 30)
        payload = json.loads(output.read_text(encoding="utf-8"))
        self.assertIn("metrics", payload)
        self.assertIn("reports", payload)
        self.assertIn("benchmark_real_world_eval", payload["reports"])


if __name__ == "__main__":
    unittest.main()
