#!/usr/bin/env bash
set -euo pipefail

THIS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_ROOT="$(cd "$THIS_DIR/.." && pwd)"

# shellcheck disable=SC1091
source "$INSTALL_ROOT/config/install.env"

"$INSTALL_ROOT/.venv/bin/python" -m codex_issue_memory.maintenance backup
