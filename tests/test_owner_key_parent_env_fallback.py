from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from codex_issue_memory.settings import Settings


_ENV_KEYS = (
    "CODEX_HOME",
    "CODEX_THREAD_ID",
    "ISSUE_MEMORY_MAIN_CONVERSATION_KEY",
    "ISSUE_MEMORY_MAIN_CONVERSATION_KEY_ENV",
    "ISSUE_MEMORY_MAIN_CONVERSATION_ROLE",
    "ISSUE_MEMORY_SERVER_ALLOW_SYNTHETIC_OWNER_KEY",
    "ISSUE_MEMORY_SERVER_OWNER_KEY",
    "ISSUE_MEMORY_SERVER_OWNER_KEY_ENV",
    "ISSUE_MEMORY_SERVER_OWNER_ROLE",
    "ISSUE_MEMORY_MCP_OWNER_KEY",
    "ISSUE_MEMORY_MCP_OWNER_KEY_ENV",
    "ISSUE_MEMORY_MCP_OWNER_ROLE",
)


class ParentProcessOwnerKeyFallbackTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base = Path(self.temp_dir.name)
        self.original_env = {key: os.environ.get(key) for key in _ENV_KEYS}
        for key in _ENV_KEYS:
            os.environ.pop(key, None)
        os.environ["CODEX_HOME"] = str(self.base / ".codex")

    def tearDown(self) -> None:
        for key in _ENV_KEYS:
            value = self.original_env.get(key)
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self.temp_dir.cleanup()

    def _write_session(self, thread_id: str, *, forked_from_id: str = "") -> None:
        sessions_root = self.base / ".codex" / "sessions" / "2026" / "03" / "29"
        sessions_root.mkdir(parents=True, exist_ok=True)
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
        record = {
            "timestamp": "2026-03-29T00:00:00Z",
            "type": "session_meta",
            "payload": payload,
        }
        session_path = sessions_root / f"rollout-2026-03-29T00-00-00-{thread_id}.jsonl"
        session_path.write_text(json.dumps(record) + "\n", encoding="utf-8")

    def test_current_process_explicit_owner_key_wins_over_parent_fallback(self) -> None:
        self._write_session("main-conversation-parent")
        self._write_session(
            "subagent-thread-parent", forked_from_id="main-conversation-parent"
        )
        os.environ["ISSUE_MEMORY_MAIN_CONVERSATION_KEY"] = "explicit-main-42"
        os.environ["ISSUE_MEMORY_MAIN_CONVERSATION_ROLE"] = "main"
        with patch(
            "codex_issue_memory.settings._iter_parent_process_environments",
            return_value=[{"CODEX_THREAD_ID": "subagent-thread-parent"}],
        ):
            settings = Settings.from_env()
        self.assertEqual(settings.server_owner_key, "explicit-main-42")
        self.assertEqual(
            settings.server_owner_key_env, "ISSUE_MEMORY_MAIN_CONVERSATION_KEY"
        )
        self.assertEqual(settings.server_owner_role, "main")

    def test_parent_process_codex_thread_can_resolve_owner_key_when_child_env_is_replaced(
        self,
    ) -> None:
        self._write_session("main-conversation-42")
        self._write_session("subagent-thread-42", forked_from_id="main-conversation-42")
        with patch(
            "codex_issue_memory.settings._iter_parent_process_environments",
            return_value=[{"CODEX_THREAD_ID": "subagent-thread-42"}],
        ):
            settings = Settings.from_env()
        self.assertEqual(settings.server_owner_key, "main-conversation-42")
        self.assertEqual(settings.server_owner_key_env, "CODEX_THREAD_ID")
        self.assertEqual(settings.server_owner_role, "subagent")

    def test_missing_parent_lineage_keeps_owner_key_empty(self) -> None:
        with patch(
            "codex_issue_memory.settings._iter_parent_process_environments",
            return_value=[{"CODEX_THREAD_ID": "missing-thread-42"}],
        ):
            settings = Settings.from_env()
        self.assertEqual(settings.server_owner_key, "")
        self.assertEqual(settings.server_owner_key_env, "")
        self.assertEqual(settings.server_owner_role, "")

    def test_recent_codex_sessions_can_break_tie_when_env_is_missing_everywhere(
        self,
    ) -> None:
        self._write_session("main-conversation-99")
        self._write_session("subagent-thread-99", forked_from_id="main-conversation-99")
        with patch(
            "codex_issue_memory.settings._iter_parent_process_environments",
            return_value=[],
        ):
            settings = Settings.from_env()
        self.assertEqual(settings.server_owner_key, "main-conversation-99")
        self.assertEqual(settings.server_owner_key_env, "CODEX_THREAD_ID")
        self.assertEqual(settings.server_owner_role, "")

    def test_recent_codex_sessions_refuse_to_guess_when_multiple_roots_are_recent(
        self,
    ) -> None:
        self._write_session("main-conversation-a")
        self._write_session("main-conversation-b")
        with patch(
            "codex_issue_memory.settings._iter_parent_process_environments",
            return_value=[],
        ):
            settings = Settings.from_env()
        self.assertEqual(settings.server_owner_key, "")
        self.assertEqual(settings.server_owner_key_env, "")
        self.assertEqual(settings.server_owner_role, "")

    def test_opt_in_synthetic_owner_key_supports_contextless_launcher(self) -> None:
        os.environ["ISSUE_MEMORY_SERVER_REQUIRE_OWNER_KEY"] = "1"
        os.environ["ISSUE_MEMORY_SERVER_ALLOW_SYNTHETIC_OWNER_KEY"] = "1"
        with patch(
            "codex_issue_memory.settings._iter_parent_process_environments",
            return_value=[],
        ):
            settings = Settings.from_env()
        self.assertTrue(settings.server_owner_key.startswith("synthetic-process-"))
        self.assertEqual(
            settings.server_owner_key_env, "ISSUE_MEMORY_SYNTHETIC_OWNER_KEY"
        )
        self.assertEqual(settings.server_owner_role, "anonymous")


if __name__ == "__main__":
    unittest.main()
