from __future__ import annotations

import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from codex_issue_memory.backup import BackupManager
from codex_issue_memory.maintenance import cmd_doctor, cmd_recommended_config
from codex_issue_memory.storage import IssueMemoryStore


class Phase8RolloutTests(unittest.TestCase):
    _ENV_KEYS = (
        "ISSUE_MEMORY_HOME",
        "ISSUE_MEMORY_DB_PATH",
        "ISSUE_MEMORY_STATE_DIR",
        "ISSUE_MEMORY_BACKUP_DIR",
        "ISSUE_MEMORY_LOG_DIR",
        "ISSUE_MEMORY_CALIBRATION_PROFILE_PATH",
        "ISSUE_MEMORY_ENABLE_CALIBRATION_PROFILE",
    )

    def setUp(self) -> None:
        self._env_backup = {key: os.environ.get(key) for key in self._ENV_KEYS}
        self.temp_dir = tempfile.TemporaryDirectory(prefix="issue-memory-phase8-")
        base = Path(self.temp_dir.name)
        self.install_root = base / "install"
        self.install_root.mkdir(parents=True, exist_ok=True)
        self.data_root = base / "share"
        self.state_root = base / "state"
        self.backup_root = self.data_root / "backups"
        self.codex_home = base / ".codex"
        self.state_root.mkdir(parents=True, exist_ok=True)
        self.backup_root.mkdir(parents=True, exist_ok=True)
        os.environ["ISSUE_MEMORY_HOME"] = str(self.data_root)
        os.environ["ISSUE_MEMORY_DB_PATH"] = str(self.data_root / "issue_memory.sqlite3")
        os.environ["ISSUE_MEMORY_STATE_DIR"] = str(self.state_root)
        os.environ["ISSUE_MEMORY_BACKUP_DIR"] = str(self.backup_root)
        os.environ["ISSUE_MEMORY_LOG_DIR"] = str(self.state_root / "log")
        os.environ["ISSUE_MEMORY_ENABLE_CALIBRATION_PROFILE"] = "1"
        os.environ["ISSUE_MEMORY_CALIBRATION_PROFILE_PATH"] = str(self.state_root / "calibration_profile.json")
        store = IssueMemoryStore.from_env()
        store.initialize()
        Path(os.environ["ISSUE_MEMORY_CALIBRATION_PROFILE_PATH"]).write_text(
            json.dumps({"global": {"accept_threshold": 0.68, "weak_threshold": 0.40, "ambiguity_margin": 0.09}}),
            encoding="utf-8",
        )
        BackupManager.from_env().create_backup()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()
        for key, value in self._env_backup.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def _register_codex(self) -> None:
        subprocess.run(
            [
                sys.executable,
                "scripts/register_codex.py",
                "--install-root",
                str(self.install_root),
                "--data-root",
                str(self.data_root),
                "--state-root",
                str(self.state_root),
                "--codex-home",
                str(self.codex_home),
            ],
            check=True,
            cwd=Path(__file__).resolve().parents[1],
        )

    def test_recommended_config_emits_shadow_defaults(self) -> None:
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            cmd_recommended_config(mode="shadow", fmt="json", max_instances=0)
        payload = json.loads(buffer.getvalue())
        self.assertEqual(payload["mode"], "shadow")
        self.assertEqual(payload["env"]["ISSUE_MEMORY_ENABLE_STRATEGY_BANDIT"], "1")
        self.assertEqual(payload["env"]["ISSUE_MEMORY_ENABLE_STRATEGY_BANDIT_SHADOW_MODE"], "1")
        self.assertIsNone(payload["max_instances"])
        self.assertEqual(payload["env"]["ISSUE_MEMORY_SERVER_REQUIRE_OWNER_KEY"], "1")
        self.assertEqual(payload["env"]["ISSUE_MEMORY_MAX_MCP_INSTANCES"], "0")
        self.assertTrue(payload["env"]["ISSUE_MEMORY_SERVER_LOCK_DIR"].endswith("/state/run"))
        self.assertEqual(payload["env"]["ISSUE_MEMORY_SERVER_DUPLICATE_EXIT_CODE"], "75")
        self.assertEqual(payload["env"]["ISSUE_MEMORY_SERVER_OWNER_KEY_ENV"], "ISSUE_MEMORY_MAIN_CONVERSATION_KEY")

    def test_doctor_passes_for_registered_shadow_rollout(self) -> None:
        self._register_codex()
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            cmd_doctor(mode="shadow", max_instances=0, codex_home=str(self.codex_home))
        payload = json.loads(buffer.getvalue())
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["summary"]["errors"], 0)
        self.assertEqual(payload["summary"]["warnings"], 0)
        self.assertIsNone(payload["expected_max_instances"])

    def test_doctor_fails_when_owner_key_env_is_missing_from_registered_config(self) -> None:
        self._register_codex()
        config_path = self.codex_home / "config.toml"
        config_text = config_path.read_text(encoding="utf-8").replace(
            'ISSUE_MEMORY_SERVER_OWNER_KEY_ENV = "ISSUE_MEMORY_MAIN_CONVERSATION_KEY"\n',
            "",
        )
        config_path.write_text(config_text, encoding="utf-8")

        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            cmd_doctor(mode="shadow", max_instances=0, codex_home=str(self.codex_home))
        payload = json.loads(buffer.getvalue())

        self.assertEqual(payload["status"], "fail")
        self.assertGreaterEqual(payload["summary"]["errors"], 1)
        owner_env_checks = [item for item in payload["checks"] if item["name"] == "env:ISSUE_MEMORY_SERVER_OWNER_KEY_ENV"]
        self.assertEqual(len(owner_env_checks), 1)
        self.assertFalse(owner_env_checks[0]["ok"])

    def test_register_codex_writes_recommended_flags(self) -> None:
        self._register_codex()
        config_text = (self.codex_home / "config.toml").read_text(encoding="utf-8")
        self.assertIn('ISSUE_MEMORY_MAX_MCP_INSTANCES = "0"', config_text)
        self.assertIn('ISSUE_MEMORY_ENABLE_STRATEGY_BANDIT = "1"', config_text)
        self.assertIn('ISSUE_MEMORY_ENABLE_STRATEGY_BANDIT_SHADOW_MODE = "1"', config_text)
        self.assertIn('ISSUE_MEMORY_SERVER_LOCK_DIR = "', config_text)
        self.assertIn('ISSUE_MEMORY_SERVER_DUPLICATE_EXIT_CODE = "75"', config_text)
        self.assertIn('ISSUE_MEMORY_SERVER_REQUIRE_OWNER_KEY = "1"', config_text)
        self.assertIn('ISSUE_MEMORY_SERVER_OWNER_KEY_ENV = "ISSUE_MEMORY_MAIN_CONVERSATION_KEY"', config_text)

    def test_maintenance_cli_runs_e2e_mcp_reuse_harness(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        env = os.environ.copy()
        src_root = str(repo_root / "src")
        env["PYTHONPATH"] = src_root + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
        proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "codex_issue_memory.maintenance",
                "e2e-mcp-reuse-harness",
                "--json",
                "--timeout",
                "10",
            ],
            cwd=repo_root,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, msg=proc.stderr)
        payload = json.loads(proc.stdout)
        verdict = payload["verdict"]
        self.assertTrue(verdict["main_started"])
        self.assertTrue(verdict["subagent_resolved_to_main"])
        self.assertTrue(verdict["duplicate_launch_rejected"])
        self.assertTrue(verdict["reuse_signal_emitted"])
        self.assertTrue(verdict["distinct_main_conversations_coexist"])


if __name__ == "__main__":
    unittest.main()
