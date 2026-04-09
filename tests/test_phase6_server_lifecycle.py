from __future__ import annotations

import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from typing import Any

from codex_issue_memory.lifecycle import read_server_lifecycle_status
from codex_issue_memory.settings import Settings


class ServerLifecycleTests(unittest.TestCase):
    _ENV_KEYS = (
        "ISSUE_MEMORY_HOME",
        "ISSUE_MEMORY_DB_PATH",
        "ISSUE_MEMORY_STATE_DIR",
        "ISSUE_MEMORY_BACKUP_DIR",
        "ISSUE_MEMORY_LOG_DIR",
        "ISSUE_MEMORY_ENFORCE_SINGLE_MCP_INSTANCE",
        "ISSUE_MEMORY_MAX_MCP_INSTANCES",
        "ISSUE_MEMORY_SERVER_LOCK_DIR",
        "ISSUE_MEMORY_SERVER_DUPLICATE_EXIT_CODE",
        "ISSUE_MEMORY_SERVER_REQUIRE_OWNER_KEY",
        "ISSUE_MEMORY_MAIN_CONVERSATION_KEY",
        "ISSUE_MEMORY_MAIN_CONVERSATION_KEY_ENV",
        "ISSUE_MEMORY_MAIN_CONVERSATION_ROLE",
        "ISSUE_MEMORY_SERVER_OWNER_KEY",
        "ISSUE_MEMORY_SERVER_OWNER_KEY_ENV",
        "ISSUE_MEMORY_SERVER_OWNER_ROLE",
        "ISSUE_MEMORY_MCP_OWNER_KEY",
        "ISSUE_MEMORY_MCP_OWNER_KEY_ENV",
        "ISSUE_MEMORY_SERVER_ENFORCE_PARENT_SINGLETON",
        "ISSUE_MEMORY_SERVER_PARENT_INSTANCE_IDLE_TIMEOUT_SECONDS",
        "ISSUE_MEMORY_SERVER_PARENT_INSTANCE_MONITOR_INTERVAL_SECONDS",
        "CODEX_CONVERSATION_ID",
        "CODEX_HOME",
        "CODEX_THREAD_ID",
    )

    def setUp(self) -> None:
        self._env_backup = {key: os.environ.get(key) for key in self._ENV_KEYS}
        self.temp_dir = tempfile.TemporaryDirectory(prefix="issue-memory-server-lifecycle-")
        base = Path(self.temp_dir.name)
        os.environ["ISSUE_MEMORY_HOME"] = str(base / "share")
        os.environ["ISSUE_MEMORY_DB_PATH"] = str(base / "share" / "issue_memory.sqlite3")
        os.environ["ISSUE_MEMORY_STATE_DIR"] = str(base / "state")
        os.environ["ISSUE_MEMORY_BACKUP_DIR"] = str(base / "share" / "backups")
        os.environ["ISSUE_MEMORY_LOG_DIR"] = str(base / "state" / "log")
        os.environ["ISSUE_MEMORY_SERVER_LOCK_DIR"] = str(base / "state" / "run")
        os.environ["ISSUE_MEMORY_SERVER_DUPLICATE_EXIT_CODE"] = "75"
        os.environ["ISSUE_MEMORY_SERVER_REQUIRE_OWNER_KEY"] = "0"
        os.environ["ISSUE_MEMORY_ENFORCE_SINGLE_MCP_INSTANCE"] = "1"
        os.environ["ISSUE_MEMORY_MAX_MCP_INSTANCES"] = "1"
        os.environ["CODEX_HOME"] = str(base / ".codex")
        os.environ["ISSUE_MEMORY_SERVER_ENFORCE_PARENT_SINGLETON"] = "0"
        os.environ["ISSUE_MEMORY_SERVER_PARENT_INSTANCE_IDLE_TIMEOUT_SECONDS"] = "0"
        os.environ["ISSUE_MEMORY_SERVER_PARENT_INSTANCE_MONITOR_INTERVAL_SECONDS"] = "0.25"
        os.environ.pop("CODEX_THREAD_ID", None)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()
        for key, value in self._env_backup.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def test_server_uses_lazy_singleton_app_and_updates_status(self) -> None:
        import codex_issue_memory.server as server

        server = importlib.reload(server)
        lifecycle = server.get_lifecycle()
        lifecycle.start()
        try:
            first = server.get_app()
            second = server.get_app()
            self.assertIs(first, second)
            status = read_server_lifecycle_status(Settings.from_env()).to_dict()
            self.assertTrue(status["running"])
            self.assertTrue(status["lock_acquired"])
            self.assertIsNotNone(status["initialized_at"])
            self.assertEqual(status["max_instances"], 1)
            self.assertEqual(status["active_count"], 1)
            self.assertTrue(Path(status["status_path"]).exists())
        finally:
            lifecycle.release()
        stopped = read_server_lifecycle_status(Settings.from_env()).to_dict()
        self.assertFalse(stopped["running"])
        self.assertEqual(stopped["active_count"], 0)

    def test_two_slot_cap_allows_two_processes_and_rejects_third(self) -> None:
        os.environ["ISSUE_MEMORY_ENFORCE_SINGLE_MCP_INSTANCE"] = "0"
        os.environ["ISSUE_MEMORY_MAX_MCP_INSTANCES"] = "2"
        repo_src = str(Path(__file__).resolve().parents[1] / "src")
        child_code = (
            "import signal, sys, time\n"
            "from codex_issue_memory.lifecycle import MCPServerLifecycle, MCPServerOwnerConflict\n"
            "from codex_issue_memory.settings import Settings\n"
            "l = MCPServerLifecycle(Settings.from_env())\n"
            "try:\n"
            "    l.start()\n"
            "    print(f'STARTED:{l._slot}', flush=True)\n"
            "    signal.signal(signal.SIGTERM, lambda *args: sys.exit(0))\n"
            "    signal.signal(signal.SIGINT, lambda *args: sys.exit(0))\n"
            "    while True:\n"
            "        time.sleep(0.2)\n"
            "except MCPServerOwnerConflict as exc:\n"
            "    print(f'ERROR:{exc}', file=sys.stderr, flush=True)\n"
            "    sys.exit(exc.exit_code)\n"
            "except Exception as exc:\n"
            "    print(f'ERROR:{exc}', file=sys.stderr, flush=True)\n"
            "    sys.exit(1)\n"
        )
        env = os.environ.copy()
        env["PYTHONPATH"] = repo_src + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")

        procs: list[subprocess.Popen[str]] = []
        try:
            for _ in range(2):
                proc = subprocess.Popen(
                    [sys.executable, "-c", child_code],
                    env=env,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
                line = proc.stdout.readline().strip() if proc.stdout is not None else ""
                self.assertTrue(line.startswith("STARTED:"), msg=f"unexpected child output: {line}")
                procs.append(proc)
            status = read_server_lifecycle_status(Settings.from_env()).to_dict()
            self.assertTrue(status["running"])
            self.assertEqual(status["max_instances"], 2)
            self.assertEqual(status["active_count"], 2)
            self.assertEqual(sorted(slot["slot"] for slot in status["active_slots"]), [0, 1])

            third = subprocess.Popen(
                [sys.executable, "-c", child_code],
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            return_code = third.wait(timeout=5)
            stderr = third.stderr.read() if third.stderr is not None else ""
            self.assertNotEqual(return_code, 0)
            self.assertIn("instance cap reached", stderr)
        finally:
            for proc in procs:
                proc.terminate()
            deadline = time.time() + 5
            while time.time() < deadline and any(proc.poll() is None for proc in procs):
                time.sleep(0.1)
            for proc in procs:
                if proc.poll() is None:
                    proc.kill()
            stopped = read_server_lifecycle_status(Settings.from_env()).to_dict()
            self.assertEqual(stopped["active_count"], 0)

    def test_owner_key_rejects_duplicate_without_global_cap_and_distinct_keys_can_coexist(self) -> None:
        os.environ["ISSUE_MEMORY_ENFORCE_SINGLE_MCP_INSTANCE"] = "0"
        os.environ["ISSUE_MEMORY_MAX_MCP_INSTANCES"] = "0"
        os.environ["ISSUE_MEMORY_SERVER_REQUIRE_OWNER_KEY"] = "1"
        repo_src = str(Path(__file__).resolve().parents[1] / "src")
        child_code = (
            "import signal, sys, time\n"
            "from codex_issue_memory.lifecycle import MCPServerLifecycle, MCPServerOwnerConflict\n"
            "from codex_issue_memory.settings import Settings\n"
            "l = MCPServerLifecycle(Settings.from_env())\n"
            "try:\n"
            "    l.start()\n"
            "    print(f'STARTED:{l._slot}', flush=True)\n"
            "    signal.signal(signal.SIGTERM, lambda *args: sys.exit(0))\n"
            "    signal.signal(signal.SIGINT, lambda *args: sys.exit(0))\n"
            "    while True:\n"
            "        time.sleep(0.2)\n"
            "except MCPServerOwnerConflict as exc:\n"
            "    print(f'ERROR:{exc}', file=sys.stderr, flush=True)\n"
            "    sys.exit(exc.exit_code)\n"
            "except Exception as exc:\n"
            "    print(f'ERROR:{exc}', file=sys.stderr, flush=True)\n"
            "    sys.exit(1)\n"
        )
        base_env = os.environ.copy()
        base_env["PYTHONPATH"] = repo_src + (os.pathsep + base_env["PYTHONPATH"] if base_env.get("PYTHONPATH") else "")

        procs: list[subprocess.Popen[str]] = []
        try:
            for owner_key in (
                "main-conversation-a",
                "main-conversation-b",
                "main-conversation-c",
                "main-conversation-d",
            ):
                child_env = dict(base_env)
                child_env["ISSUE_MEMORY_MAIN_CONVERSATION_KEY"] = owner_key
                child_env["ISSUE_MEMORY_MAIN_CONVERSATION_ROLE"] = "main"
                proc = subprocess.Popen(
                    [sys.executable, "-c", child_code],
                    env=child_env,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
                line = proc.stdout.readline().strip() if proc.stdout is not None else ""
                self.assertTrue(line.startswith("STARTED:"), msg=f"unexpected child output: {line}")
                procs.append(proc)

            duplicate = subprocess.Popen(
                [sys.executable, "-c", child_code],
                env={
                    **base_env,
                    "ISSUE_MEMORY_MAIN_CONVERSATION_KEY": "main-conversation-a",
                    "ISSUE_MEMORY_MAIN_CONVERSATION_ROLE": "subagent",
                },
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            return_code = duplicate.wait(timeout=5)
            stderr = duplicate.stderr.read() if duplicate.stderr is not None else ""
            self.assertEqual(return_code, 75)
            self.assertIn("owner key already active", stderr)
            self.assertIn("main-conversation-a", stderr)

            status = read_server_lifecycle_status(Settings.from_env()).to_dict()
            self.assertEqual(status["max_instances"], None)
            self.assertEqual(status["active_count"], 4)
            owner_keys = {slot.get("owner_key") for slot in status["active_slots"]}
            self.assertEqual(
                owner_keys,
                {
                    "main-conversation-a",
                    "main-conversation-b",
                    "main-conversation-c",
                    "main-conversation-d",
                },
            )
            owner_roles = {slot.get("owner_role") for slot in status["active_slots"]}
            self.assertEqual(owner_roles, {"main"})
        finally:
            for proc in procs:
                proc.terminate()
            deadline = time.time() + 5
            while time.time() < deadline and any(proc.poll() is None for proc in procs):
                time.sleep(0.1)
            for proc in procs:
                if proc.poll() is None:
                    proc.kill()


    def test_parent_singleton_guard_rejects_duplicate_with_exit_code_75(self) -> None:
        os.environ["ISSUE_MEMORY_SERVER_ENFORCE_PARENT_SINGLETON"] = "1"
        os.environ["ISSUE_MEMORY_ENFORCE_SINGLE_MCP_INSTANCE"] = "0"
        os.environ["ISSUE_MEMORY_MAX_MCP_INSTANCES"] = "0"
        os.environ["ISSUE_MEMORY_SERVER_REQUIRE_OWNER_KEY"] = "0"

        repo_src = str(Path(__file__).resolve().parents[1] / "src")
        child_code = (
            "import signal, sys, time\n"
            "from codex_issue_memory.lifecycle import MCPServerLifecycle, MCPServerOwnerConflict\n"
            "from codex_issue_memory.settings import Settings\n"
            "l = MCPServerLifecycle(Settings.from_env())\n"
            "try:\n"
            "    l.start()\n"
            "    print(f'STARTED:{l._slot}', flush=True)\n"
            "    signal.signal(signal.SIGTERM, lambda *args: sys.exit(0))\n"
            "    while True:\n"
            "        time.sleep(0.2)\n"
            "except MCPServerOwnerConflict as exc:\n"
            "    print(f'ERROR:{exc}', file=sys.stderr, flush=True)\n"
            "    sys.exit(exc.exit_code)\n"
            "except Exception as exc:\n"
            "    print(f'ERROR:{exc}', file=sys.stderr, flush=True)\n"
            "    sys.exit(1)\n"
        )

        env = os.environ.copy()
        env["PYTHONPATH"] = repo_src + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")

        procs: list[subprocess.Popen[str]] = []
        duplicate: subprocess.Popen[str] | None = None
        try:
            proc = subprocess.Popen(
                [sys.executable, "-c", child_code],
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            procs.append(proc)
            started = proc.stdout.readline().strip() if proc.stdout is not None else ""
            self.assertTrue(started.startswith("STARTED:"), msg=f"unexpected first child output: {started}")

            duplicate = subprocess.Popen(
                [sys.executable, "-c", child_code],
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            return_code = duplicate.wait(timeout=8)
            stderr = duplicate.stderr.read() if duplicate.stderr is not None else ""
            self.assertEqual(return_code, 75)
            self.assertIn("parent process already has active instance", stderr)

            status = read_server_lifecycle_status(Settings.from_env()).to_dict()
            self.assertTrue(status["running"])
            self.assertEqual(status["active_count"], 1)
            self.assertTrue(status["parent_singleton_enforced"])
            self.assertIsNone(status["shutdown_reason"])
        finally:
            for proc in procs:
                proc.terminate()
            if duplicate is not None and duplicate.poll() is None:
                duplicate.terminate()
            deadline = time.time() + 5
            while time.time() < deadline and any(proc.poll() is None for proc in procs):
                time.sleep(0.1)
            for proc in procs:
                if proc.poll() is None:
                    proc.kill()

    def test_stale_slot_cleanup_recovers_dead_process_slots(self) -> None:
        os.environ["ISSUE_MEMORY_ENFORCE_SINGLE_MCP_INSTANCE"] = "0"
        os.environ["ISSUE_MEMORY_MAX_MCP_INSTANCES"] = "4"
        os.environ["ISSUE_MEMORY_SERVER_ENFORCE_PARENT_SINGLETON"] = "0"
        os.environ["ISSUE_MEMORY_SERVER_REQUIRE_OWNER_KEY"] = "0"

        repo_src = str(Path(__file__).resolve().parents[1] / "src")
        child_code = (
            "import signal, sys, time\n"
            "from codex_issue_memory.lifecycle import MCPServerLifecycle\n"
            "from codex_issue_memory.settings import Settings\n"
            "l = MCPServerLifecycle(Settings.from_env())\n"
            "l.start()\n"
            "print(f'STARTED:{l._slot}', flush=True)\n"
            "signal.signal(signal.SIGTERM, lambda *args: sys.exit(0))\n"
            "while True:\n"
            "    time.sleep(0.2)\n"
        )
        env = os.environ.copy()
        env["PYTHONPATH"] = repo_src + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")

        proc = subprocess.Popen(
            [sys.executable, "-c", child_code],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            started = proc.stdout.readline().strip() if proc.stdout is not None else ""
            self.assertTrue(started.startswith("STARTED:"), msg=f"unexpected child output: {started}")
        finally:
            proc.kill()
            proc.wait(timeout=5)
            time.sleep(0.5)

            status = read_server_lifecycle_status(Settings.from_env()).to_dict()
            self.assertFalse(status["running"])
            self.assertEqual(status["active_count"], 0)

            slot_files = sorted((Path(os.environ["ISSUE_MEMORY_STATE_DIR"]) / "mcp_slots").glob("issue_memory_mcp_slot_*.json"))
            self.assertTrue(slot_files, "expected slot status file to exist")
            payload = json.loads(slot_files[0].read_text(encoding="utf-8"))
            self.assertFalse(payload.get("running", False))
            self.assertEqual(payload.get("shutdown_reason"), "stale-slot-pid-gone")

    def test_idle_timeout_stops_instance(self) -> None:
        os.environ["ISSUE_MEMORY_SERVER_ENFORCE_PARENT_SINGLETON"] = "0"
        os.environ["ISSUE_MEMORY_SERVER_PARENT_INSTANCE_IDLE_TIMEOUT_SECONDS"] = "1"
        os.environ["ISSUE_MEMORY_SERVER_PARENT_INSTANCE_MONITOR_INTERVAL_SECONDS"] = "0.2"

        repo_src = str(Path(__file__).resolve().parents[1] / "src")
        child_code = (
            "import time\n"
            "from codex_issue_memory.lifecycle import MCPServerLifecycle\n"
            "from codex_issue_memory.settings import Settings\n"
            "l = MCPServerLifecycle(Settings.from_env())\n"
            "l.start()\n"
            "l.mark_initialized()\n"
            "print('READY', flush=True)\n"
            "while True:\n"
            "    time.sleep(0.2)\n"
        )
        env = os.environ.copy()
        env["PYTHONPATH"] = repo_src + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
        proc = subprocess.Popen(
            [sys.executable, "-c", child_code],
            env=env,
            cwd=Path(__file__).resolve().parents[1],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            ready = proc.stdout.readline().strip() if proc.stdout is not None else ""
            self.assertEqual(ready, "READY")
            deadline = time.time() + 6
            status_dict: dict[str, Any] | None = None
            while time.time() < deadline:
                status_dict = read_server_lifecycle_status(Settings.from_env()).to_dict()
                if not status_dict["running"] and status_dict["shutdown_reason"] == "idle-timeout":
                    break
                time.sleep(0.2)
            if status_dict is None:
                self.fail("did not observe idle-timeout shutdown state")
            status = status_dict
            self.assertFalse(status["running"])
            self.assertEqual(status["shutdown_reason"], "idle-timeout")
        finally:
            if proc.stdin is not None and not proc.stdin.closed:
                proc.stdin.close()
            deadline = time.time() + 5
            while time.time() < deadline and proc.poll() is None:
                time.sleep(0.1)
            if proc.poll() is None:
                proc.terminate()
                time.sleep(0.5)
            if proc.poll() is None:
                proc.kill()

    def test_parent_death_shuts_down_instance(self) -> None:
        os.environ["ISSUE_MEMORY_SERVER_ENFORCE_PARENT_SINGLETON"] = "1"
        os.environ["ISSUE_MEMORY_SERVER_PARENT_INSTANCE_MONITOR_INTERVAL_SECONDS"] = "0.2"
        os.environ["ISSUE_MEMORY_SERVER_PARENT_INSTANCE_IDLE_TIMEOUT_SECONDS"] = "0"

        repo_src = str(Path(__file__).resolve().parents[1] / "src")
        scripts_dir = Path(self.temp_dir.name) / "mcp-lifecycle-parent-death"
        scripts_dir.mkdir(parents=True, exist_ok=True)
        server_script = scripts_dir / "server_runner.py"
        wrapper_script = scripts_dir / "launch_and_exit.py"
        server_script.write_text(
            "import time\n"
            "from codex_issue_memory.lifecycle import MCPServerLifecycle\n"
            "from codex_issue_memory.settings import Settings\n"
            "l = MCPServerLifecycle(Settings.from_env())\n"
            "l.start()\n"
            "l.mark_initialized()\n"
            "while True:\n"
            "    time.sleep(0.2)\n",
            encoding="utf-8",
        )
        wrapper_script.write_text(
            "import os\n"
            "import subprocess\n"
            "import sys\n"
            "import time\n"
            f"server = {str(server_script)!r}\n"
            "env = os.environ.copy()\n"
            "proc = subprocess.Popen([sys.executable, server], env=env)\n"
            "print(proc.pid, flush=True)\n"
            "time.sleep(1.0)\n",
            encoding="utf-8",
        )

        launcher_env = os.environ.copy()
        launcher_env["PYTHONPATH"] = repo_src + (os.pathsep + launcher_env["PYTHONPATH"] if launcher_env.get("PYTHONPATH") else "")
        launcher = subprocess.run(
            [sys.executable, str(wrapper_script)],
            env=launcher_env,
            capture_output=True,
            text=True,
            check=False,
            cwd=scripts_dir,
        )
        self.assertEqual(launcher.returncode, 0, msg=launcher.stderr)
        server_pid = int((launcher.stdout or "").strip() or "0")
        self.assertGreater(server_pid, 0)

        try:
            deadline = time.time() + 6
            status_dict: dict[str, Any] | None = None
            while time.time() < deadline:
                status_dict = read_server_lifecycle_status(Settings.from_env()).to_dict()
                if not status_dict["running"] and status_dict["shutdown_reason"] == "parent-death":
                    break
                time.sleep(0.2)
            if status_dict is None:
                self.fail("did not observe parent-death shutdown state")
            status = status_dict
            self.assertFalse(status["running"])
            self.assertEqual(status["shutdown_reason"], "parent-death")
            death_deadline = time.time() + 5
            while time.time() < death_deadline:
                try:
                    os.kill(server_pid, 0)
                except OSError:
                    break
                time.sleep(0.2)
            else:
                self.fail("server process remained alive after parent-death shutdown")
        finally:
            try:
                os.kill(server_pid, 15)
            except OSError:
                pass

    def test_stdin_eof_shuts_down_instance(self) -> None:
        os.environ["ISSUE_MEMORY_SERVER_ENFORCE_PARENT_SINGLETON"] = "0"
        os.environ["ISSUE_MEMORY_SERVER_PARENT_INSTANCE_MONITOR_INTERVAL_SECONDS"] = "0.2"
        os.environ["ISSUE_MEMORY_SERVER_PARENT_INSTANCE_IDLE_TIMEOUT_SECONDS"] = "0"

        repo_src = str(Path(__file__).resolve().parents[1] / "src")
        child_code = (
            "import time\n"
            "from codex_issue_memory.lifecycle import MCPServerLifecycle\n"
            "from codex_issue_memory.settings import Settings\n"
            "l = MCPServerLifecycle(Settings.from_env())\n"
            "l.start()\n"
            "l.mark_initialized()\n"
            "print('READY', flush=True)\n"
            "try:\n"
            "    while True:\n"
            "        time.sleep(0.2)\n"
            "except KeyboardInterrupt:\n"
            "    print(f'SHUTDOWN:{l.shutdown_reason}', flush=True)\n"
            "finally:\n"
            "    l.release()\n"
        )
        env = os.environ.copy()
        env["PYTHONPATH"] = repo_src + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
        proc = subprocess.Popen(
            [sys.executable, "-c", child_code],
            env=env,
            cwd=Path(__file__).resolve().parents[1],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        ready = proc.stdout.readline().strip() if proc.stdout is not None else ""
        self.assertEqual(ready, "READY")
        assert proc.stdin is not None
        proc.stdin.close()
        proc.wait(timeout=8)
        stdout = proc.stdout.read() if proc.stdout is not None else ""
        self.assertEqual(proc.returncode, 0, msg=proc.stderr.read() if proc.stderr is not None else "")
        self.assertIn("SHUTDOWN:stdin-eof", stdout)

    def test_owner_only_mode_requires_owner_key(self) -> None:
        os.environ["ISSUE_MEMORY_ENFORCE_SINGLE_MCP_INSTANCE"] = "0"
        os.environ["ISSUE_MEMORY_MAX_MCP_INSTANCES"] = "0"
        os.environ["ISSUE_MEMORY_SERVER_REQUIRE_OWNER_KEY"] = "1"
        repo_src = str(Path(__file__).resolve().parents[1] / "src")
        env = os.environ.copy()
        env["PYTHONPATH"] = repo_src + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
        proc = subprocess.run(
            [sys.executable, "-m", "codex_issue_memory.server"],
            env=env,
            cwd=Path(__file__).resolve().parents[1],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("owner key is required but missing", proc.stderr)

    def test_settings_can_resolve_owner_key_from_main_conversation_named_env_var(self) -> None:
        os.environ["ISSUE_MEMORY_MAIN_CONVERSATION_KEY"] = ""
        os.environ["ISSUE_MEMORY_MAIN_CONVERSATION_KEY_ENV"] = "CODEX_PARENT_CONVERSATION_ID"
        os.environ["CODEX_PARENT_CONVERSATION_ID"] = "main-conversation-42"
        settings = Settings.from_env()
        self.assertEqual(settings.server_owner_key_env, "CODEX_PARENT_CONVERSATION_ID")
        self.assertEqual(settings.server_owner_key, "main-conversation-42")

    def test_settings_can_resolve_owner_key_from_compat_named_env_var(self) -> None:
        os.environ["ISSUE_MEMORY_SERVER_OWNER_KEY"] = ""
        os.environ["ISSUE_MEMORY_SERVER_OWNER_KEY_ENV"] = "CODEX_CONVERSATION_ID"
        os.environ["CODEX_CONVERSATION_ID"] = "conversation-42"
        settings = Settings.from_env()
        self.assertEqual(settings.server_owner_key_env, "CODEX_CONVERSATION_ID")
        self.assertEqual(settings.server_owner_key, "conversation-42")

    def test_settings_do_not_derive_owner_key_from_bare_codex_thread_id_without_session_lineage(self) -> None:
        os.environ["CODEX_THREAD_ID"] = "subagent-thread-42"
        settings = Settings.from_env()
        self.assertEqual(settings.server_owner_key_env, "")
        self.assertEqual(settings.server_owner_key, "")
        self.assertEqual(settings.server_owner_role, "")

    def test_settings_can_resolve_owner_key_from_codex_thread_lineage(self) -> None:
        base = Path(self.temp_dir.name)
        sessions_root = base / ".codex" / "sessions" / "2026" / "03" / "29"
        sessions_root.mkdir(parents=True, exist_ok=True)

        def write_session(thread_id: str, *, forked_from_id: str = "") -> None:
            payload: dict[str, object] = {
                "id": thread_id,
                "timestamp": "2026-03-29T00:00:00Z",
                "cwd": str(Path(__file__).resolve().parents[1]),
                "originator": "codex_vscode",
                "cli_version": "test",
                "source": "vscode",
            }
            if forked_from_id:
                payload["forked_from_id"] = forked_from_id
                payload["source"] = {
                    "subagent": {
                        "thread_spawn": {
                            "parent_thread_id": forked_from_id,
                            "depth": 1,
                            "agent_role": "explorer",
                        }
                    }
                }
            record = {"timestamp": "2026-03-29T00:00:00Z", "type": "session_meta", "payload": payload}
            (sessions_root / f"rollout-2026-03-29T00-00-00-{thread_id}.jsonl").write_text(
                json.dumps(record) + "\n",
                encoding="utf-8",
            )

        write_session("main-conversation-42")
        write_session("subagent-thread-42", forked_from_id="main-conversation-42")
        os.environ["CODEX_THREAD_ID"] = "subagent-thread-42"
        settings = Settings.from_env()
        self.assertEqual(settings.server_owner_key_env, "CODEX_THREAD_ID")
        self.assertEqual(settings.server_owner_key, "main-conversation-42")
        self.assertEqual(settings.server_owner_role, "subagent")

    def test_settings_can_resolve_nested_codex_thread_lineage_without_suffix_collision(self) -> None:
        base = Path(self.temp_dir.name)
        sessions_root = base / ".codex" / "sessions" / "2026" / "03" / "29"
        sessions_root.mkdir(parents=True, exist_ok=True)

        def write_session(thread_id: str, *, forked_from_id: str = "", parent_thread_id: str = "", depth: int = 0) -> None:
            payload: dict[str, object] = {
                "id": thread_id,
                "timestamp": "2026-03-29T00:00:00Z",
                "cwd": str(Path(__file__).resolve().parents[1]),
                "originator": "codex_vscode",
                "cli_version": "test",
                "source": "vscode",
            }
            if forked_from_id:
                payload["forked_from_id"] = forked_from_id
                payload["source"] = {
                    "subagent": {
                        "thread_spawn": {
                            "parent_thread_id": parent_thread_id or forked_from_id,
                            "depth": depth,
                            "agent_role": "explorer",
                        }
                    }
                }
            record = {"timestamp": "2026-03-29T00:00:00Z", "type": "session_meta", "payload": payload}
            (sessions_root / f"rollout-2026-03-29T00-00-00-{thread_id}.jsonl").write_text(
                json.dumps(record) + "\n",
                encoding="utf-8",
            )

        write_session("main-conversation-42")
        write_session("subagent-thread-a", forked_from_id="main-conversation-42", parent_thread_id="main-conversation-42", depth=1)
        write_session("nested-subagent-thread-a", forked_from_id="subagent-thread-a", parent_thread_id="subagent-thread-a", depth=2)
        os.environ["CODEX_THREAD_ID"] = "nested-subagent-thread-a"

        settings = Settings.from_env()
        self.assertEqual(settings.server_owner_key_env, "CODEX_THREAD_ID")
        self.assertEqual(settings.server_owner_key, "main-conversation-42")
        self.assertEqual(settings.server_owner_role, "subagent")

    def test_codex_thread_lineage_rejects_subagent_duplicate_without_global_cap(self) -> None:
        os.environ["ISSUE_MEMORY_ENFORCE_SINGLE_MCP_INSTANCE"] = "0"
        os.environ["ISSUE_MEMORY_MAX_MCP_INSTANCES"] = "0"
        os.environ["ISSUE_MEMORY_SERVER_REQUIRE_OWNER_KEY"] = "1"
        codex_home = Path(os.environ["CODEX_HOME"])
        sessions_root = codex_home / "sessions" / "2026" / "03" / "29"
        sessions_root.mkdir(parents=True, exist_ok=True)

        def write_session(thread_id: str, *, forked_from_id: str = "", parent_thread_id: str = "") -> None:
            payload: dict[str, object] = {
                "id": thread_id,
                "timestamp": "2026-03-29T00:00:00Z",
                "cwd": str(Path(__file__).resolve().parents[1]),
                "originator": "codex_vscode",
                "cli_version": "test",
                "source": "vscode",
            }
            if forked_from_id:
                payload["forked_from_id"] = forked_from_id
                payload["source"] = {
                    "subagent": {
                        "thread_spawn": {
                            "parent_thread_id": parent_thread_id or forked_from_id,
                            "depth": 1,
                            "agent_role": "explorer",
                        }
                    }
                }
            record = {"timestamp": "2026-03-29T00:00:00Z", "type": "session_meta", "payload": payload}
            (sessions_root / f"rollout-2026-03-29T00-00-00-{thread_id}.jsonl").write_text(
                json.dumps(record) + "\n",
                encoding="utf-8",
            )

        main_a = "main-conversation-a"
        main_b = "main-conversation-b"
        subagent_a = "subagent-thread-a"
        write_session(main_a)
        write_session(main_b)
        write_session(subagent_a, forked_from_id=main_a, parent_thread_id=main_a)

        repo_src = str(Path(__file__).resolve().parents[1] / "src")
        child_code = (
            "import signal, sys, time\n"
            "from codex_issue_memory.lifecycle import MCPServerLifecycle, MCPServerOwnerConflict\n"
            "from codex_issue_memory.settings import Settings\n"
            "l = MCPServerLifecycle(Settings.from_env())\n"
            "try:\n"
            "    l.start()\n"
            "    print(f'STARTED:{l._slot}', flush=True)\n"
            "    signal.signal(signal.SIGTERM, lambda *args: sys.exit(0))\n"
            "    signal.signal(signal.SIGINT, lambda *args: sys.exit(0))\n"
            "    while True:\n"
            "        time.sleep(0.2)\n"
            "except MCPServerOwnerConflict as exc:\n"
            "    print(f'ERROR:{exc}', file=sys.stderr, flush=True)\n"
            "    sys.exit(exc.exit_code)\n"
            "except Exception as exc:\n"
            "    print(f'ERROR:{exc}', file=sys.stderr, flush=True)\n"
            "    sys.exit(1)\n"
        )
        base_env = os.environ.copy()
        base_env["PYTHONPATH"] = repo_src + (os.pathsep + base_env["PYTHONPATH"] if base_env.get("PYTHONPATH") else "")

        procs: list[subprocess.Popen[str]] = []
        try:
            for thread_id in (main_a, main_b):
                child_env = dict(base_env)
                child_env["CODEX_THREAD_ID"] = thread_id
                proc = subprocess.Popen(
                    [sys.executable, "-c", child_code],
                    env=child_env,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
                line = proc.stdout.readline().strip() if proc.stdout is not None else ""
                self.assertTrue(line.startswith("STARTED:"), msg=f"unexpected child output: {line}")
                procs.append(proc)

            duplicate = subprocess.Popen(
                [sys.executable, "-c", child_code],
                env={**base_env, "CODEX_THREAD_ID": subagent_a},
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            return_code = duplicate.wait(timeout=5)
            stderr = duplicate.stderr.read() if duplicate.stderr is not None else ""
            self.assertEqual(return_code, 75)
            self.assertIn("owner key already active", stderr)
            self.assertIn(main_a, stderr)

            status = read_server_lifecycle_status(Settings.from_env()).to_dict()
            self.assertEqual(status["active_count"], 2)
            self.assertEqual(status["max_instances"], None)
            owner_keys = {slot.get("owner_key") for slot in status["active_slots"]}
            self.assertEqual(owner_keys, {main_a, main_b})
        finally:
            for proc in procs:
                proc.terminate()
            deadline = time.time() + 5
            while time.time() < deadline and any(proc.poll() is None for proc in procs):
                time.sleep(0.1)
            for proc in procs:
                if proc.poll() is None:
                    proc.kill()

    def test_status_reports_owner_metadata(self) -> None:
        os.environ["ISSUE_MEMORY_MAIN_CONVERSATION_KEY"] = "main-conversation-42"
        os.environ["ISSUE_MEMORY_MAIN_CONVERSATION_ROLE"] = "main"

        import codex_issue_memory.server as server

        server = importlib.reload(server)
        lifecycle = server.get_lifecycle()
        lifecycle.start()
        try:
            status = read_server_lifecycle_status(Settings.from_env()).to_dict()
            self.assertEqual(status["owner_key"], "main-conversation-42")
            self.assertEqual(status["owner_key_env"], "ISSUE_MEMORY_MAIN_CONVERSATION_KEY")
            self.assertEqual(status["owner_role"], "main")
            self.assertIsInstance(status["parent_pid"], int)
            self.assertGreater(status["parent_pid"], 0)
            self.assertEqual(status["active_slots"][0]["owner_key"], "main-conversation-42")
            self.assertEqual(status["active_slots"][0]["owner_key_env"], "ISSUE_MEMORY_MAIN_CONVERSATION_KEY")
            self.assertEqual(status["active_slots"][0]["owner_role"], "main")
            self.assertIsInstance(status["active_slots"][0]["parent_pid"], int)
            self.assertGreater(status["active_slots"][0]["parent_pid"], 0)
        finally:
            lifecycle.release()


if __name__ == "__main__":
    unittest.main()
