#!/usr/bin/env bash
set -euo pipefail

SCRIPT_SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

INSTALL_ROOT="${INSTALL_ROOT:-$HOME/infra/codex-issue-memory}"
DATA_ROOT="${DATA_ROOT:-$HOME/.local/share/codex-issue-memory}"
STATE_ROOT="${STATE_ROOT:-$HOME/.local/state/codex-issue-memory}"
BACKUP_ROOT="${BACKUP_ROOT:-$DATA_ROOT/backups}"
WINDOWS_BACKUP_TARGET="${WINDOWS_BACKUP_TARGET:-}"
CODEX_HOME="${CODEX_HOME:-$HOME/.codex}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
SKIP_DEP_INSTALL="${SKIP_DEP_INSTALL:-0}"
SKIP_CRON_INSTALL="${SKIP_CRON_INSTALL:-0}"

RAW_INSTALL_ROOT="${INSTALL_ROOT%/}"
INSTALL_ROOT_CANON="$(realpath -m -- "$INSTALL_ROOT")"
SCRIPT_SOURCE_CANON="$(realpath -m -- "$SCRIPT_SOURCE_DIR")"
HOME_CANON="$(realpath -m -- "$HOME")"
COPY_IN_PLACE=0
case "$RAW_INSTALL_ROOT" in
  ""|"/"|"."|".."|"$HOME")
    echo "Refusing destructive copy for dangerous INSTALL_ROOT: $INSTALL_ROOT" >&2
    exit 1
    ;;
esac
case "$INSTALL_ROOT_CANON" in
  ""|"/"|"$HOME_CANON")
    echo "Refusing destructive copy for dangerous INSTALL_ROOT: $INSTALL_ROOT" >&2
    exit 1
    ;;
esac
if [[ "$INSTALL_ROOT_CANON" == "$SCRIPT_SOURCE_CANON" ]]; then
  COPY_IN_PLACE=1
elif [[ "$INSTALL_ROOT_CANON" == "$SCRIPT_SOURCE_CANON"/* ]] || [[ "$SCRIPT_SOURCE_CANON" == "$INSTALL_ROOT_CANON"/* ]]; then
  echo "Refusing destructive copy between nested source/install paths: $INSTALL_ROOT" >&2
  exit 1
fi

echo "[1/7] Creating directory layout..."
mkdir -p "$INSTALL_ROOT" "$DATA_ROOT" "$STATE_ROOT" "$BACKUP_ROOT" "$CODEX_HOME"

echo "[2/7] Copying bundle into install root..."
if [[ "$COPY_IN_PLACE" == "1" ]]; then
  echo "Install root is the source directory; skipping bundle copy."
elif command -v rsync >/dev/null 2>&1; then
  rsync -a --delete \
    --exclude ".venv" \
    --exclude "__pycache__" \
    --exclude ".pytest_cache" \
    --exclude "dist" \
    --exclude "build" \
    "$SCRIPT_SOURCE_DIR"/ "$INSTALL_ROOT"/
else
  rm -rf "$INSTALL_ROOT"
  mkdir -p "$(dirname "$INSTALL_ROOT")"
  cp -a "$SCRIPT_SOURCE_DIR" "$INSTALL_ROOT"
fi

echo "[3/7] Creating Python environment..."
"$PYTHON_BIN" -m venv "$INSTALL_ROOT/.venv"
"$INSTALL_ROOT/.venv/bin/python" -m pip install --upgrade pip setuptools wheel
if [[ "$SKIP_DEP_INSTALL" == "1" ]]; then
  "$INSTALL_ROOT/.venv/bin/python" -m pip install -e "$INSTALL_ROOT" --no-deps
else
  "$INSTALL_ROOT/.venv/bin/python" -m pip install -e "$INSTALL_ROOT"
fi

echo "[4/7] Writing install environment..."
mkdir -p "$INSTALL_ROOT/config"
cat > "$INSTALL_ROOT/config/install.env" <<EOF
export INSTALL_ROOT="$INSTALL_ROOT"
export DATA_ROOT="$DATA_ROOT"
export STATE_ROOT="$STATE_ROOT"
export BACKUP_ROOT="$BACKUP_ROOT"
export WINDOWS_BACKUP_TARGET="$WINDOWS_BACKUP_TARGET"
export CODEX_HOME="$CODEX_HOME"
export ISSUE_MEMORY_HOME="$DATA_ROOT"
export ISSUE_MEMORY_DB_PATH="$DATA_ROOT/issue_memory.sqlite3"
export ISSUE_MEMORY_STATE_DIR="$STATE_ROOT"
export ISSUE_MEMORY_BACKUP_DIR="$BACKUP_ROOT"
export ISSUE_MEMORY_WINDOWS_BACKUP_TARGET="$WINDOWS_BACKUP_TARGET"
export ISSUE_MEMORY_LOCAL_BACKUP_KEEP="\${ISSUE_MEMORY_LOCAL_BACKUP_KEEP:-30}"
export ISSUE_MEMORY_MIRROR_BACKUP_KEEP="\${ISSUE_MEMORY_MIRROR_BACKUP_KEEP:-15}"
export ISSUE_MEMORY_LOG_DIR="$STATE_ROOT/log"
export ISSUE_MEMORY_SERVER_LOCK_DIR="$STATE_ROOT/run"
export ISSUE_MEMORY_SERVER_DUPLICATE_EXIT_CODE="${ISSUE_MEMORY_SERVER_DUPLICATE_EXIT_CODE:-75}"
export ISSUE_MEMORY_SERVER_REQUIRE_OWNER_KEY="${ISSUE_MEMORY_SERVER_REQUIRE_OWNER_KEY:-1}"
export ISSUE_MEMORY_SERVER_OWNER_KEY_ENV="${ISSUE_MEMORY_SERVER_OWNER_KEY_ENV:-ISSUE_MEMORY_MAIN_CONVERSATION_KEY}"
export ISSUE_MEMORY_SERVER_ALLOW_SYNTHETIC_OWNER_KEY="${ISSUE_MEMORY_SERVER_ALLOW_SYNTHETIC_OWNER_KEY:-1}"
export ISSUE_MEMORY_SERVER_ENFORCE_PARENT_SINGLETON="${ISSUE_MEMORY_SERVER_ENFORCE_PARENT_SINGLETON:-1}"
export ISSUE_MEMORY_SERVER_PARENT_INSTANCE_IDLE_TIMEOUT_SECONDS="${ISSUE_MEMORY_SERVER_PARENT_INSTANCE_IDLE_TIMEOUT_SECONDS:-0}"
export ISSUE_MEMORY_SERVER_PARENT_INSTANCE_MONITOR_INTERVAL_SECONDS="${ISSUE_MEMORY_SERVER_PARENT_INSTANCE_MONITOR_INTERVAL_SECONDS:-1.0}"
export ISSUE_MEMORY_ENFORCE_SINGLE_MCP_INSTANCE="${ISSUE_MEMORY_ENFORCE_SINGLE_MCP_INSTANCE:-0}"
export ISSUE_MEMORY_MAX_MCP_INSTANCES="${ISSUE_MEMORY_MAX_MCP_INSTANCES:-2}"
export ISSUE_MEMORY_ENABLE_CALIBRATION_PROFILE="${ISSUE_MEMORY_ENABLE_CALIBRATION_PROFILE:-1}"
export ISSUE_MEMORY_CALIBRATION_PROFILE_PATH="$STATE_ROOT/calibration_profile.json"
EOF

echo "[5/7] Initializing database..."
# shellcheck disable=SC1091
source "$INSTALL_ROOT/config/install.env"
"$INSTALL_ROOT/.venv/bin/python" -m codex_issue_memory.maintenance init-db

echo "[6/7] Updating Codex config and global instructions..."
"$INSTALL_ROOT/.venv/bin/python" "$INSTALL_ROOT/scripts/register_codex.py" \
  --install-root "$INSTALL_ROOT" \
  --data-root "$DATA_ROOT" \
  --state-root "$STATE_ROOT" \
  --codex-home "$CODEX_HOME"

echo "[7/7] Installing cron backup..."
if [[ "$SKIP_CRON_INSTALL" == "1" ]]; then
  echo "Skipping cron installation because SKIP_CRON_INSTALL=1"
else
  bash "$INSTALL_ROOT/scripts/install_cron.sh" || true
fi

cat <<EOF

Install complete.

Install root:        $INSTALL_ROOT
Data root:           $DATA_ROOT
State root:          $STATE_ROOT
Backup root:         $BACKUP_ROOT
Windows mirror root: ${WINDOWS_BACKUP_TARGET:-<not configured>}
Codex home:          $CODEX_HOME

Next:
  1. Restart Codex (or open a new Codex session).
  2. Run: bash $INSTALL_ROOT/scripts/verify_install.sh
  3. Optionally inspect: $CODEX_HOME/config.toml
EOF
