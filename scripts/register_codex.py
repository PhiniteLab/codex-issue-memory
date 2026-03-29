#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

CONFIG_BEGIN = "# >>> codex-issue-memory >>>"
CONFIG_END = "# <<< codex-issue-memory <<<"
AGENTS_BEGIN = "<!-- >>> codex-issue-memory >>> -->"
AGENTS_END = "<!-- <<< codex-issue-memory <<< -->"  # replaced below


def replace_block(text: str, begin: str, end: str, new_block: str) -> str:
    if begin in text and end in text:
        prefix = text.split(begin, 1)[0]
        suffix = text.split(end, 1)[1]
        return prefix + new_block + suffix
    if text and not text.endswith("\n"):
        text += "\n"
    return text + ("\n" if text else "") + new_block


def main() -> None:
    parser = argparse.ArgumentParser(description="Register Codex MCP config and AGENTS instructions.")
    parser.add_argument("--install-root", required=True)
    parser.add_argument("--data-root", required=True)
    parser.add_argument("--state-root", required=True)
    parser.add_argument("--codex-home", required=True)
    args = parser.parse_args()

    install_root = Path(args.install_root).expanduser().resolve()
    data_root = Path(args.data_root).expanduser().resolve()
    state_root = Path(args.state_root).expanduser().resolve()
    codex_home = Path(args.codex_home).expanduser().resolve()

    codex_home.mkdir(parents=True, exist_ok=True)

    config_path = codex_home / "config.toml"
    config_path.touch(exist_ok=True)
    config_text = config_path.read_text(encoding="utf-8")

    config_block = f"""{CONFIG_BEGIN}
[mcp_servers.issue_memory]
command = "{install_root / ".venv" / "bin" / "python"}"
args = ["-m", "codex_issue_memory.server"]
cwd = "{install_root}"
startup_timeout_sec = 15
tool_timeout_sec = 25
enabled = true
required = false
[mcp_servers.issue_memory.env]
ISSUE_MEMORY_HOME = "{data_root}"
ISSUE_MEMORY_DB_PATH = "{data_root / "issue_memory.sqlite3"}"
ISSUE_MEMORY_STATE_DIR = "{state_root}"
ISSUE_MEMORY_BACKUP_DIR = "{data_root / "backups"}"
ISSUE_MEMORY_LOG_DIR = "{state_root / "log"}"
ISSUE_MEMORY_SERVER_LOCK_DIR = "{state_root / "run"}"
ISSUE_MEMORY_SERVER_DUPLICATE_EXIT_CODE = "75"
ISSUE_MEMORY_SERVER_REQUIRE_OWNER_KEY = "1"
ISSUE_MEMORY_SERVER_OWNER_KEY_ENV = "ISSUE_MEMORY_MAIN_CONVERSATION_KEY"
ISSUE_MEMORY_ENFORCE_SINGLE_MCP_INSTANCE = "0"
ISSUE_MEMORY_MAX_MCP_INSTANCES = "0"
ISSUE_MEMORY_ENABLE_STRATEGY_BANDIT = "1"
ISSUE_MEMORY_ENABLE_STRATEGY_BANDIT_SHADOW_MODE = "1"
ISSUE_MEMORY_ENABLE_PREFERENCE_RULES = "1"
ISSUE_MEMORY_ENABLE_REDACTION = "1"
ISSUE_MEMORY_ENABLE_CALIBRATION_PROFILE = "1"
ISSUE_MEMORY_CALIBRATION_PROFILE_PATH = "{state_root / "calibration_profile.json"}"
# Prefer explicit ISSUE_MEMORY_MAIN_CONVERSATION_KEY injection per main conversation.
# Current Codex runtimes may also derive the main-conversation key from CODEX_THREAD_ID session lineage.
# Optional diagnostics: ISSUE_MEMORY_MAIN_CONVERSATION_ROLE=main|subagent
# Do not set ISSUE_MEMORY_SERVER_OWNER_KEY to a static literal in config.toml.
{CONFIG_END}
"""
    config_text = replace_block(config_text, CONFIG_BEGIN, CONFIG_END, config_block)
    config_path.write_text(config_text, encoding="utf-8")

    agents_path = codex_home / "AGENTS.md"
    agents_path.touch(exist_ok=True)
    agents_text = agents_path.read_text(encoding="utf-8")

    agents_begin = "<!-- >>> codex-issue-memory >>> -->"
    agents_end = "<!-- <<< codex-issue-memory <<< -->"
    agents_block = f"""{agents_begin}

## Issue-memory workflow

- When a command fails, a test fails, or a stack trace appears, call the `issue_memory` MCP server before deep debugging.
- Use `issue_match` first with:
  - the raw error or the shortest meaningful failing excerpt,
  - the command that failed,
  - the relevant file path if known,
  - `project_scope` equal to the repo name for repo-specific issues, or `global` for reusable cross-repo issues.
- Read only the top one or two compact matches first. Call `issue_get` only if the shortlist is ambiguous.
- After a fix is verified, call `issue_record_resolution` only if the fix is reusable or prevents a recurring class of failures.
- Keep writes compact:
  - canonical symptom,
  - root cause class,
  - canonical fix,
  - prevention rule,
  - validation steps.
- Never dump long logs into issue memory when a short normalized excerpt is enough.
- Prefer project-scoped memory for repo-specific path/config/import details.
- Prefer global memory for reusable engineering rules and failure families.

{agents_end}
"""
    agents_text = replace_block(agents_text, agents_begin, agents_end, agents_block)
    agents_path.write_text(agents_text, encoding="utf-8")


if __name__ == "__main__":
    main()
