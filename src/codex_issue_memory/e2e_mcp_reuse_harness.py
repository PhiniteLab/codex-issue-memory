from __future__ import annotations

import argparse
import json
import os
from contextlib import contextmanager
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from typing import Any, Iterator, Sequence, cast

from .lifecycle import read_server_lifecycle_status
from .settings import Settings


StatusDict = dict[str, Any]
PROJECT_ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOT = PROJECT_ROOT / "src"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Exercise the end-to-end MCP reuse contract for main/subagent ownership.",
    )
    parser.add_argument("--timeout", type=float, default=10.0, help="Per-wait timeout in seconds.")
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of human-readable text.")
    return parser


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    return build_parser().parse_args(argv)


def write_session(
    sessions_root: Path,
    thread_id: str,
    *,
    forked_from_id: str = "",
    agent_role: str = "explorer",
) -> None:
    payload: StatusDict = {
        "id": thread_id,
        "timestamp": "2026-03-29T00:00:00Z",
        "cwd": str(PROJECT_ROOT),
        "originator": "codex_vscode",
        "cli_version": "harness",
        "source": "vscode",
    }
    if forked_from_id:
        payload["forked_from_id"] = forked_from_id
        payload["source"] = {
            "subagent": {
                "thread_spawn": {
                    "parent_thread_id": forked_from_id,
                    "depth": 1,
                    "agent_role": agent_role,
                }
            }
        }
    record = {"timestamp": "2026-03-29T00:00:00Z", "type": "session_meta", "payload": payload}
    sessions_root.mkdir(parents=True, exist_ok=True)
    (sessions_root / f"harness-2026-03-29T00-00-00-{thread_id}.jsonl").write_text(
        json.dumps(record) + "\n",
        encoding="utf-8",
    )


@contextmanager
def patched_env(overrides: dict[str, str]) -> Iterator[None]:
    previous = {key: os.environ.get(key) for key in overrides}
    try:
        os.environ.update(overrides)
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def build_base_env(base: Path) -> dict[str, str]:
    env = os.environ.copy()
    pythonpath_entries: list[str] = []
    if SOURCE_ROOT.exists():
        pythonpath_entries.append(str(SOURCE_ROOT))
    if env.get("PYTHONPATH"):
        pythonpath_entries.append(env["PYTHONPATH"])
    env.update(
        {
            "ISSUE_MEMORY_HOME": str(base / "share"),
            "ISSUE_MEMORY_DB_PATH": str(base / "share" / "issue_memory.sqlite3"),
            "ISSUE_MEMORY_STATE_DIR": str(base / "state"),
            "ISSUE_MEMORY_BACKUP_DIR": str(base / "share" / "backups"),
            "ISSUE_MEMORY_LOG_DIR": str(base / "state" / "log"),
            "ISSUE_MEMORY_SERVER_LOCK_DIR": str(base / "state" / "run"),
            "ISSUE_MEMORY_SERVER_DUPLICATE_EXIT_CODE": "75",
            "ISSUE_MEMORY_SERVER_REQUIRE_OWNER_KEY": "1",
            "ISSUE_MEMORY_SERVER_OWNER_KEY_ENV": "ISSUE_MEMORY_MAIN_CONVERSATION_KEY",
            "ISSUE_MEMORY_ENFORCE_SINGLE_MCP_INSTANCE": "0",
            "ISSUE_MEMORY_MAX_MCP_INSTANCES": "0",
            "CODEX_HOME": str(base / ".codex"),
        }
    )
    if pythonpath_entries:
        env["PYTHONPATH"] = os.pathsep.join(pythonpath_entries)
    return env


def choose_repo_python(repo_root: Path) -> str:
    candidates = [
        repo_root / ".venv" / "bin" / "python",
        repo_root / "venv" / "bin" / "python",
        repo_root / ".venv" / "Scripts" / "python.exe",
        repo_root / "venv" / "Scripts" / "python.exe",
    ]
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    return sys.executable


def wait_for_active_count(base_env: dict[str, str], expected: int, timeout: float) -> StatusDict:
    deadline = time.time() + timeout
    last_status: StatusDict = {}
    with patched_env(
        {
            "ISSUE_MEMORY_HOME": base_env["ISSUE_MEMORY_HOME"],
            "ISSUE_MEMORY_DB_PATH": base_env["ISSUE_MEMORY_DB_PATH"],
            "ISSUE_MEMORY_STATE_DIR": base_env["ISSUE_MEMORY_STATE_DIR"],
            "ISSUE_MEMORY_BACKUP_DIR": base_env["ISSUE_MEMORY_BACKUP_DIR"],
            "ISSUE_MEMORY_LOG_DIR": base_env["ISSUE_MEMORY_LOG_DIR"],
            "ISSUE_MEMORY_SERVER_LOCK_DIR": base_env["ISSUE_MEMORY_SERVER_LOCK_DIR"],
            "ISSUE_MEMORY_SERVER_DUPLICATE_EXIT_CODE": base_env["ISSUE_MEMORY_SERVER_DUPLICATE_EXIT_CODE"],
            "ISSUE_MEMORY_SERVER_REQUIRE_OWNER_KEY": base_env["ISSUE_MEMORY_SERVER_REQUIRE_OWNER_KEY"],
            "ISSUE_MEMORY_ENFORCE_SINGLE_MCP_INSTANCE": base_env["ISSUE_MEMORY_ENFORCE_SINGLE_MCP_INSTANCE"],
            "ISSUE_MEMORY_MAX_MCP_INSTANCES": base_env["ISSUE_MEMORY_MAX_MCP_INSTANCES"],
            "CODEX_HOME": base_env["CODEX_HOME"],
        }
    ):
        while time.time() < deadline:
            last_status = read_server_lifecycle_status(Settings.from_env()).to_dict()
            if int(cast(int, last_status.get("active_count", 0) or 0)) == expected:
                return last_status
            time.sleep(0.1)
    raise TimeoutError(f"Timed out waiting for active_count={expected}; last_status={last_status}")


def terminate_processes(processes: list[subprocess.Popen[str]]) -> None:
    for proc in processes:
        if proc.poll() is None:
            proc.terminate()
    deadline = time.time() + 5
    while time.time() < deadline and any(proc.poll() is None for proc in processes):
        time.sleep(0.1)
    for proc in processes:
        if proc.poll() is None:
            proc.kill()


def _active_owner_keys(status: StatusDict) -> list[str]:
    active_slots = cast(list[dict[str, Any]], status.get("active_slots", []))
    return sorted(
        str(slot.get("owner_key") or "")
        for slot in active_slots
        if str(slot.get("owner_key") or "")
    )


def run_harness(timeout: float) -> StatusDict:
    with tempfile.TemporaryDirectory(prefix="issue-memory-e2e-reuse-harness-") as temp_dir:
        base = Path(temp_dir)
        base_env = build_base_env(base)
        sessions_root = Path(base_env["CODEX_HOME"]) / "sessions" / "2026" / "03" / "29"

        main_thread = "main-conversation-harness-a"
        subagent_thread = "subagent-thread-harness-a"
        sibling_main_thread = "main-conversation-harness-b"
        write_session(sessions_root, main_thread)
        write_session(sessions_root, subagent_thread, forked_from_id=main_thread, agent_role="validator")
        write_session(sessions_root, sibling_main_thread)

        server_cmd = [choose_repo_python(PROJECT_ROOT), "-m", "codex_issue_memory.server"]
        processes: list[subprocess.Popen[str]] = []
        payload: StatusDict = {
            "repo_root": str(PROJECT_ROOT),
            "server_command": server_cmd,
            "main_thread": main_thread,
            "subagent_thread": subagent_thread,
            "sibling_main_thread": sibling_main_thread,
            "preferred_owner_key_env": "ISSUE_MEMORY_MAIN_CONVERSATION_KEY",
            "duplicate_exit_code_expected": 75,
        }

        try:
            main_proc = subprocess.Popen(
                server_cmd,
                cwd=PROJECT_ROOT,
                env={
                    **base_env,
                    "ISSUE_MEMORY_MAIN_CONVERSATION_KEY": main_thread,
                    "ISSUE_MEMORY_MAIN_CONVERSATION_ROLE": "main",
                },
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            processes.append(main_proc)
            first_status = wait_for_active_count(base_env, expected=1, timeout=timeout)
            payload["main_status"] = first_status

            with patched_env({**base_env, "CODEX_THREAD_ID": subagent_thread}):
                subagent_settings = Settings.from_env()
            payload["subagent_resolution"] = {
                "owner_key_env": subagent_settings.server_owner_key_env,
                "owner_key": subagent_settings.server_owner_key,
                "owner_role": subagent_settings.server_owner_role,
            }

            duplicate = subprocess.run(
                server_cmd,
                cwd=PROJECT_ROOT,
                env={
                    **base_env,
                    "ISSUE_MEMORY_MAIN_CONVERSATION_KEY": main_thread,
                    "ISSUE_MEMORY_MAIN_CONVERSATION_ROLE": "subagent",
                },
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=timeout,
                check=False,
            )
            payload["duplicate_launch"] = {
                "returncode": duplicate.returncode,
                "stdout": duplicate.stdout.strip(),
                "stderr": duplicate.stderr.strip(),
            }
            post_duplicate_status = wait_for_active_count(base_env, expected=1, timeout=timeout)
            payload["post_duplicate_status"] = post_duplicate_status

            sibling_proc = subprocess.Popen(
                server_cmd,
                cwd=PROJECT_ROOT,
                env={
                    **base_env,
                    "ISSUE_MEMORY_MAIN_CONVERSATION_KEY": sibling_main_thread,
                    "ISSUE_MEMORY_MAIN_CONVERSATION_ROLE": "main",
                },
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            processes.append(sibling_proc)
            second_status = wait_for_active_count(base_env, expected=2, timeout=timeout)
            payload["coexistence_status"] = second_status

            active_owner_keys = _active_owner_keys(second_status)
            post_duplicate_owner_keys = _active_owner_keys(post_duplicate_status)
            payload["verdict"] = {
                "main_started": bool(first_status.get("running")),
                "subagent_resolved_to_main": subagent_settings.server_owner_key == main_thread,
                "duplicate_launch_rejected": duplicate.returncode == 75,
                "reuse_signal_emitted": duplicate.returncode == 75,
                "duplicate_preserved_single_owner_slot": post_duplicate_owner_keys == [main_thread],
                "distinct_main_conversations_coexist": active_owner_keys == sorted(
                    [main_thread, sibling_main_thread]
                ),
                "active_owner_keys": active_owner_keys,
            }
            return payload
        finally:
            terminate_processes(processes)


def render_human(payload: StatusDict) -> str:
    verdict = cast(dict[str, Any], payload.get("verdict", {}))
    duplicate = cast(dict[str, Any], payload.get("duplicate_launch", {}))
    resolution = cast(dict[str, Any], payload.get("subagent_resolution", {}))
    active_owner_keys = cast(list[str], verdict.get("active_owner_keys", []))
    lines = [
        f"main_started: {verdict.get('main_started')}",
        f"subagent_resolved_to_main: {verdict.get('subagent_resolved_to_main')}",
        f"duplicate_launch_rejected: {verdict.get('duplicate_launch_rejected')}",
        f"reuse_signal_emitted: {verdict.get('reuse_signal_emitted')}",
        f"distinct_main_conversations_coexist: {verdict.get('distinct_main_conversations_coexist')}",
        f"active_owner_keys: {', '.join(active_owner_keys)}",
        "",
        f"subagent owner_key_env: {resolution.get('owner_key_env')}",
        f"subagent owner_key: {resolution.get('owner_key')}",
        f"subagent owner_role: {resolution.get('owner_role')}",
        "",
        f"duplicate returncode: {duplicate.get('returncode')}",
        f"duplicate stderr: {duplicate.get('stderr')}",
    ]
    return "\n".join(lines)


def harness_succeeded(payload: StatusDict) -> bool:
    verdict = cast(dict[str, Any], payload.get("verdict", {}))
    return all(
        bool(verdict.get(key))
        for key in (
            "main_started",
            "subagent_resolved_to_main",
            "duplicate_launch_rejected",
            "reuse_signal_emitted",
            "duplicate_preserved_single_owner_slot",
            "distinct_main_conversations_coexist",
        )
    )


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    payload = run_harness(timeout=args.timeout)
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(render_human(payload))
    return 0 if harness_succeeded(payload) else 1
