#!/usr/bin/env bash
set -euo pipefail

THIS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_ROOT="$(cd "$THIS_DIR/.." && pwd)"
INSTALL_ENV="$INSTALL_ROOT/config/install.env"

if [[ -f "$INSTALL_ENV" ]]; then
  # shellcheck disable=SC1091
  source "$INSTALL_ENV"
  VERIFY_MODE="installed-bundle"
else
  VERIFY_MODE="source-checkout"
  echo "[verify] Missing install environment: $INSTALL_ENV"
  echo "[verify] Falling back to source-checkout defaults."
  export INSTALL_ROOT
  export CODEX_HOME="${CODEX_HOME:-$HOME/.codex}"
  export ISSUE_MEMORY_HOME="${ISSUE_MEMORY_HOME:-$HOME/.local/share/codex-issue-memory}"
  export ISSUE_MEMORY_DB_PATH="${ISSUE_MEMORY_DB_PATH:-$ISSUE_MEMORY_HOME/issue_memory.sqlite3}"
  export ISSUE_MEMORY_STATE_DIR="${ISSUE_MEMORY_STATE_DIR:-$HOME/.local/state/codex-issue-memory}"
  export ISSUE_MEMORY_BACKUP_DIR="${ISSUE_MEMORY_BACKUP_DIR:-$ISSUE_MEMORY_HOME/backups}"
  export ISSUE_MEMORY_LOG_DIR="${ISSUE_MEMORY_LOG_DIR:-$ISSUE_MEMORY_STATE_DIR/log}"
  export ISSUE_MEMORY_SERVER_LOCK_DIR="${ISSUE_MEMORY_SERVER_LOCK_DIR:-$ISSUE_MEMORY_STATE_DIR/run}"
fi

if [[ -x "$INSTALL_ROOT/.venv/bin/python" ]]; then
  VERIFY_PYTHON="$INSTALL_ROOT/.venv/bin/python"
else
  VERIFY_PYTHON="${VERIFY_PYTHON:-python3}"
fi

if [[ ! -f "$CODEX_HOME/config.toml" ]]; then
  echo "Expected Codex config at $CODEX_HOME/config.toml" >&2
  exit 1
fi

PYTHONPATH_PREFIX="$INSTALL_ROOT"
if [[ -d "$INSTALL_ROOT/src" ]]; then
  PYTHONPATH_PREFIX="$INSTALL_ROOT/src:$PYTHONPATH_PREFIX"
fi

echo "[verify] Mode: $VERIFY_MODE"
echo "[verify] Running local smoke test..."
PYTHONPATH="$PYTHONPATH_PREFIX${PYTHONPATH:+:$PYTHONPATH}" \
  "$VERIFY_PYTHON" -m codex_issue_memory.maintenance smoke

echo "[verify] Checking Codex config..."
CONFIG_MATCHES="$(grep -c "^\[mcp_servers.issue_memory\]" "$CODEX_HOME/config.toml")"
if [[ "$CONFIG_MATCHES" != "1" ]]; then
  echo "Expected exactly one [mcp_servers.issue_memory] block, found: $CONFIG_MATCHES" >&2
  exit 1
fi
grep -n 'ISSUE_MEMORY_SERVER_REQUIRE_OWNER_KEY = "1"' "$CODEX_HOME/config.toml"
grep -n 'ISSUE_MEMORY_SERVER_OWNER_KEY_ENV = "ISSUE_MEMORY_MAIN_CONVERSATION_KEY"' "$CODEX_HOME/config.toml"
grep -n 'ISSUE_MEMORY_SERVER_ALLOW_SYNTHETIC_OWNER_KEY = "1"' "$CODEX_HOME/config.toml"
grep -n 'ISSUE_MEMORY_MAX_MCP_INSTANCES = "0"' "$CODEX_HOME/config.toml"
grep -n 'ISSUE_MEMORY_SERVER_LOCK_DIR = "' "$CODEX_HOME/config.toml"
grep -n 'ISSUE_MEMORY_SERVER_DUPLICATE_EXIT_CODE = "75"' "$CODEX_HOME/config.toml"
grep -n 'ISSUE_MEMORY_SERVER_ENFORCE_PARENT_SINGLETON = "1"' "$CODEX_HOME/config.toml"
grep -n 'ISSUE_MEMORY_SERVER_PARENT_INSTANCE_IDLE_TIMEOUT_SECONDS = "0"' "$CODEX_HOME/config.toml"
grep -n 'ISSUE_MEMORY_SERVER_PARENT_INSTANCE_MONITOR_INTERVAL_SECONDS = "1.0"' "$CODEX_HOME/config.toml"
grep -n 'ISSUE_MEMORY_ENABLE_STRATEGY_BANDIT = "1"' "$CODEX_HOME/config.toml"
grep -n 'ISSUE_MEMORY_ENABLE_STRATEGY_BANDIT_SHADOW_MODE = "1"' "$CODEX_HOME/config.toml"

echo "[verify] Checking AGENTS snippet..."
grep -n "Issue-memory workflow" "$CODEX_HOME/AGENTS.md"

echo "[verify] Checking bundled reference skill content..."
test -f "$INSTALL_ROOT/skills/issue-memory-self-learning/SKILL.md"

echo "[verify] All local checks passed."
