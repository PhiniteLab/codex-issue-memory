from __future__ import annotations

import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import tomllib
import unittest

from codex_issue_memory.backup import BackupManager
from codex_issue_memory.settings import Settings
from codex_issue_memory.storage import IssueMemoryStore


class SettingsHardeningTests(unittest.TestCase):
    _ENV_KEYS = (
        "ISSUE_MEMORY_HOME",
        "ISSUE_MEMORY_DB_PATH",
        "ISSUE_MEMORY_STATE_DIR",
        "ISSUE_MEMORY_BACKUP_DIR",
        "ISSUE_MEMORY_LOG_DIR",
        "ISSUE_MEMORY_CALIBRATION_PROFILE_PATH",
        "ISSUE_MEMORY_LOCAL_BACKUP_KEEP",
        "ISSUE_MEMORY_MATCH_ACCEPT_THRESHOLD",
        "ISSUE_MEMORY_SESSION_TTL_SECONDS",
        "ISSUE_MEMORY_DENSE_EMBEDDING_DIM",
        "ISSUE_MEMORY_GUARDRAIL_LIMIT",
        "ISSUE_MEMORY_SESSION_DECAY_HALF_LIFE_MINUTES",
        "ISSUE_MEMORY_MAX_MCP_INSTANCES",
        "ISSUE_MEMORY_SERVER_REQUIRE_OWNER_KEY",
        "ISSUE_MEMORY_SERVER_DUPLICATE_EXIT_CODE",
        "ISSUE_MEMORY_SERVER_PARENT_INSTANCE_MONITOR_INTERVAL_SECONDS",
    )

    def setUp(self) -> None:
        self._env_backup = {key: os.environ.get(key) for key in self._ENV_KEYS}
        self.temp_dir = tempfile.TemporaryDirectory(prefix="issue-memory-hardening-")
        base = Path(self.temp_dir.name)
        os.environ["ISSUE_MEMORY_HOME"] = str(base / "share")
        os.environ["ISSUE_MEMORY_DB_PATH"] = str(
            base / "share" / "issue_memory.sqlite3"
        )
        os.environ["ISSUE_MEMORY_STATE_DIR"] = str(base / "state")
        os.environ["ISSUE_MEMORY_BACKUP_DIR"] = str(base / "share" / "backups")
        os.environ["ISSUE_MEMORY_LOG_DIR"] = str(base / "state" / "log")
        os.environ["ISSUE_MEMORY_CALIBRATION_PROFILE_PATH"] = str(
            base / "state" / "calibration_profile.json"
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()
        for key, value in self._env_backup.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def test_from_env_ignores_malformed_numeric_values(self) -> None:
        os.environ["ISSUE_MEMORY_LOCAL_BACKUP_KEEP"] = "not-an-int"
        os.environ["ISSUE_MEMORY_MATCH_ACCEPT_THRESHOLD"] = "nan"
        os.environ["ISSUE_MEMORY_SESSION_TTL_SECONDS"] = "still-not-an-int"
        os.environ["ISSUE_MEMORY_DENSE_EMBEDDING_DIM"] = "broken"
        os.environ["ISSUE_MEMORY_GUARDRAIL_LIMIT"] = "bad"
        os.environ["ISSUE_MEMORY_SESSION_DECAY_HALF_LIFE_MINUTES"] = "bad"
        os.environ["ISSUE_MEMORY_MAX_MCP_INSTANCES"] = "bad"
        os.environ["ISSUE_MEMORY_SERVER_REQUIRE_OWNER_KEY"] = "0"
        os.environ["ISSUE_MEMORY_SERVER_DUPLICATE_EXIT_CODE"] = "bad"
        os.environ[
            "ISSUE_MEMORY_SERVER_PARENT_INSTANCE_MONITOR_INTERVAL_SECONDS"
        ] = "bad"

        settings = Settings.from_env()

        self.assertEqual(settings.local_backup_keep, 30)
        self.assertAlmostEqual(settings.match_accept_threshold, 0.68)
        self.assertEqual(settings.session_ttl_seconds, 21600)
        self.assertEqual(settings.dense_embedding_dim, 192)
        self.assertEqual(settings.guardrail_limit, 5)
        self.assertAlmostEqual(settings.session_decay_half_life_minutes, 30.0)
        self.assertEqual(settings.max_mcp_instances, 2)
        self.assertEqual(settings.server_duplicate_exit_code, 75)
        self.assertAlmostEqual(
            settings.server_parent_instance_monitor_interval_seconds, 1.0
        )

    def test_from_env_preserves_existing_numeric_clamps(self) -> None:
        os.environ["ISSUE_MEMORY_DENSE_EMBEDDING_DIM"] = "1"
        os.environ["ISSUE_MEMORY_GUARDRAIL_LIMIT"] = "-10"
        os.environ["ISSUE_MEMORY_SESSION_DECAY_HALF_LIFE_MINUTES"] = "0.5"

        settings = Settings.from_env()

        self.assertEqual(settings.dense_embedding_dim, 32)
        self.assertEqual(settings.guardrail_limit, 1)
        self.assertAlmostEqual(settings.session_decay_half_life_minutes, 1.0)


class BackupHardeningTests(unittest.TestCase):
    _ENV_KEYS = (
        "ISSUE_MEMORY_HOME",
        "ISSUE_MEMORY_DB_PATH",
        "ISSUE_MEMORY_STATE_DIR",
        "ISSUE_MEMORY_BACKUP_DIR",
        "ISSUE_MEMORY_LOG_DIR",
        "ISSUE_MEMORY_CALIBRATION_PROFILE_PATH",
    )

    def setUp(self) -> None:
        self._env_backup = {key: os.environ.get(key) for key in self._ENV_KEYS}
        self.temp_dir = tempfile.TemporaryDirectory(prefix="issue-memory-backup-")
        base = Path(self.temp_dir.name)
        os.environ["ISSUE_MEMORY_HOME"] = str(base / "share")
        os.environ["ISSUE_MEMORY_DB_PATH"] = str(
            base / "share" / "issue_memory.sqlite3"
        )
        os.environ["ISSUE_MEMORY_STATE_DIR"] = str(base / "state")
        os.environ["ISSUE_MEMORY_BACKUP_DIR"] = str(base / "share" / "backups")
        os.environ["ISSUE_MEMORY_LOG_DIR"] = str(base / "state" / "log")
        os.environ["ISSUE_MEMORY_CALIBRATION_PROFILE_PATH"] = str(
            base / "state" / "calibration_profile.json"
        )
        settings = Settings.from_env()
        with sqlite3.connect(settings.db_path) as conn:
            conn.execute("CREATE TABLE example (id INTEGER PRIMARY KEY)")
            conn.commit()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()
        for key, value in self._env_backup.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def test_backup_filenames_are_unique_and_keep_existing_glob_shape(self) -> None:
        manager = BackupManager.from_env()

        first = manager.create_backup()
        second = manager.create_backup()

        first_name = Path(first.local_path).name
        second_name = Path(second.local_path).name
        self.assertNotEqual(first_name, second_name)
        self.assertRegex(first_name, r"^issue_memory_\d{8}_\d{6}_\d{6}\.sqlite3$")
        self.assertRegex(second_name, r"^issue_memory_\d{8}_\d{6}_\d{6}\.sqlite3$")
        backups = list(
            Path(os.environ["ISSUE_MEMORY_BACKUP_DIR"]).glob("issue_memory_*.sqlite3")
        )
        self.assertEqual(len(backups), 2)


class ReportPathHardeningTests(unittest.TestCase):
    _ENV_KEYS = (
        "ISSUE_MEMORY_HOME",
        "ISSUE_MEMORY_DB_PATH",
        "ISSUE_MEMORY_STATE_DIR",
        "ISSUE_MEMORY_BACKUP_DIR",
        "ISSUE_MEMORY_LOG_DIR",
        "ISSUE_MEMORY_CALIBRATION_PROFILE_PATH",
    )

    def setUp(self) -> None:
        self._env_backup = {key: os.environ.get(key) for key in self._ENV_KEYS}
        self.temp_dir = tempfile.TemporaryDirectory(prefix="issue-memory-reports-")
        base = Path(self.temp_dir.name)
        os.environ["ISSUE_MEMORY_HOME"] = str(base / "share")
        os.environ["ISSUE_MEMORY_DB_PATH"] = str(
            base / "share" / "issue_memory.sqlite3"
        )
        os.environ["ISSUE_MEMORY_STATE_DIR"] = str(base / "state")
        os.environ["ISSUE_MEMORY_BACKUP_DIR"] = str(base / "share" / "backups")
        os.environ["ISSUE_MEMORY_LOG_DIR"] = str(base / "state" / "log")
        os.environ["ISSUE_MEMORY_CALIBRATION_PROFILE_PATH"] = str(
            base / "state" / "calibration_profile.json"
        )
        self.store = IssueMemoryStore.from_env()
        self.store.initialize()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()
        for key, value in self._env_backup.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def test_save_report_rejects_path_traversal_names(self) -> None:
        for bad_name in ("", "../evil", "/tmp/evil", "nested/name", "bad\x00name"):
            with self.subTest(bad_name=bad_name):
                with self.assertRaises(ValueError):
                    self.store.save_report(bad_name, {"status": "bad"})
                with self.assertRaises(ValueError):
                    self.store.load_saved_report(bad_name)

    def test_save_report_writes_inside_report_dir(self) -> None:
        report_path = self.store.save_report("quality_gate-benchmarks.v1", {"ok": True})
        report_dir = self.store.report_dir().resolve()
        self.assertEqual(
            report_path.resolve().relative_to(report_dir).name,
            report_path.name,
        )
        self.assertEqual(
            self.store.load_saved_report("quality_gate-benchmarks.v1"), {"ok": True}
        )


class RegisterCodexEscapingTests(unittest.TestCase):
    def test_register_codex_escapes_toml_basic_strings(self) -> None:
        with tempfile.TemporaryDirectory(prefix="issue-memory-register-") as td:
            base = Path(td)
            install_root = base / 'install "quoted" \\root'
            data_root = base / 'data "quoted" \\root'
            state_root = base / 'state "quoted" \\root'
            codex_home = base / 'codex "quoted" \\root'
            install_root.mkdir(parents=True, exist_ok=True)
            data_root.mkdir(parents=True, exist_ok=True)
            state_root.mkdir(parents=True, exist_ok=True)

            subprocess.run(
                [
                    sys.executable,
                    "scripts/register_codex.py",
                    "--install-root",
                    str(install_root),
                    "--data-root",
                    str(data_root),
                    "--state-root",
                    str(state_root),
                    "--codex-home",
                    str(codex_home),
                ],
                check=True,
                cwd=Path(__file__).resolve().parents[1],
            )

            config_text = (codex_home / "config.toml").read_text(encoding="utf-8")
            self.assertIn(
                json.dumps(str(install_root.resolve() / ".venv" / "bin" / "python")),
                config_text,
            )
            self.assertIn(json.dumps(str(install_root.resolve())), config_text)
            self.assertIn(
                json.dumps(str(data_root.resolve() / "issue_memory.sqlite3")),
                config_text,
            )
            self.assertIn(
                json.dumps(str(state_root.resolve() / "calibration_profile.json")),
                config_text,
            )
            parsed = tomllib.loads(config_text)
            server = parsed["mcp_servers"]["issue_memory"]
            self.assertEqual(
                server["command"],
                str(install_root.resolve() / ".venv" / "bin" / "python"),
            )
            self.assertEqual(server["cwd"], str(install_root.resolve()))
            self.assertEqual(
                server["env"]["ISSUE_MEMORY_DB_PATH"],
                str(data_root.resolve() / "issue_memory.sqlite3"),
            )
            self.assertEqual(
                server["env"]["ISSUE_MEMORY_CALIBRATION_PROFILE_PATH"],
                str(state_root.resolve() / "calibration_profile.json"),
            )


class InstallScriptHardeningTests(unittest.TestCase):
    def test_install_script_refuses_home_as_install_root_before_copy(self) -> None:
        with tempfile.TemporaryDirectory(prefix="issue-memory-install-") as td:
            base = Path(td)
            fake_home = base / "home"
            fake_home.mkdir()
            env = os.environ.copy()
            env.update(
                {
                    "HOME": str(fake_home),
                    "INSTALL_ROOT": str(fake_home),
                    "DATA_ROOT": str(base / "data"),
                    "STATE_ROOT": str(base / "state"),
                    "BACKUP_ROOT": str(base / "data" / "backups"),
                    "CODEX_HOME": str(base / "codex"),
                    "SKIP_DEP_INSTALL": "1",
                    "SKIP_CRON_INSTALL": "1",
                }
            )

            result = subprocess.run(
                ["bash", "install.sh"],
                cwd=Path(__file__).resolve().parents[1],
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Refusing destructive copy", result.stderr)
