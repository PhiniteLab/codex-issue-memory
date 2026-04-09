# Configuration

`codex-issue-memory` is configured through environment variables plus one live Codex MCP registration.

## Public configuration model

The live public model is:

- the authoritative MCP registration is the single `issue_memory` block in `~/.codex/config.toml`
- the live custom plugin / skill root is `~/.codex/local-plugins/**`
- example files under `templates/` are reference snippets only
- runtime defaults come from `src/codex_issue_memory/settings.py`

## Live Codex registration

The repository's registration helper writes a block like this into `~/.codex/config.toml`:

```toml
[mcp_servers.issue_memory]
command = "/path/to/install/.venv/bin/python"
args = ["-m", "codex_issue_memory.server"]
cwd = "/path/to/install"
startup_timeout_sec = 15
tool_timeout_sec = 25
enabled = true
required = false

[mcp_servers.issue_memory.env]
ISSUE_MEMORY_HOME = "/path/to/data"
ISSUE_MEMORY_DB_PATH = "/path/to/data/issue_memory.sqlite3"
ISSUE_MEMORY_STATE_DIR = "/path/to/state"
ISSUE_MEMORY_BACKUP_DIR = "/path/to/data/backups"
ISSUE_MEMORY_LOG_DIR = "/path/to/state/log"
ISSUE_MEMORY_SERVER_LOCK_DIR = "/path/to/state/run"
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
ISSUE_MEMORY_CALIBRATION_PROFILE_PATH = "/path/to/state/calibration_profile.json"
```

Keep exactly one `[mcp_servers.issue_memory]` block in `~/.codex/config.toml`.

## Install-time configuration

These variables are read by `install.sh`.

| Variable | Purpose | Default | Notes |
| --- | --- | --- | --- |
| `INSTALL_ROOT` | Installed bundle location | `~/infra/codex-issue-memory` | Editable installed copy used by the generated MCP block |
| `DATA_ROOT` | Runtime data home | `~/.local/share/codex-issue-memory` | Holds the live SQLite database |
| `STATE_ROOT` | State and log home | `~/.local/state/codex-issue-memory` | Holds state files and logs |
| `BACKUP_ROOT` | Local snapshot directory | `DATA_ROOT/backups` | Local backup destination |
| `WINDOWS_BACKUP_TARGET` | Optional mirrored backup destination | unset | Mirror only; do not use as the live writable DB |
| `CODEX_HOME` | Codex home directory | `~/.codex` | Live config block is written here |
| `PYTHON_BIN` | Python executable used to create the virtualenv | `python3` | Installer helper |
| `SKIP_DEP_INSTALL` | Install with `--no-deps` | `0` | Installer helper |
| `SKIP_CRON_INSTALL` | Skip cron installation | `0` | Installer helper |

### Generated artifacts

After installation, the main generated artifacts are:

- `INSTALL_ROOT/config/install.env`
- `DATA_ROOT/issue_memory.sqlite3`
- `BACKUP_ROOT/`
- `STATE_ROOT/log/`
- `~/.codex/config.toml`
- `~/.codex/AGENTS.md`

If you rely on a custom plugin or skill wrapper, keep the live asset under `~/.codex/local-plugins/**`.

## Runtime source of truth

`Settings.from_env()` in `src/codex_issue_memory/settings.py` is the runtime source of truth. It:

- expands `~`
- applies defaults
- creates required directories
- resolves the current owner-key requirement and any optional compatibility cap
- enables or disables optional overlays and safety controls

## Core paths

| Variable | Purpose | Default |
| --- | --- | --- |
| `ISSUE_MEMORY_HOME` | Root directory for runtime data | `~/.local/share/codex-issue-memory` |
| `ISSUE_MEMORY_DB_PATH` | SQLite database file | `ISSUE_MEMORY_HOME/issue_memory.sqlite3` |
| `ISSUE_MEMORY_STATE_DIR` | State directory | `~/.local/state/codex-issue-memory` |
| `ISSUE_MEMORY_LOG_DIR` | Log directory | `ISSUE_MEMORY_STATE_DIR/log` |
| `ISSUE_MEMORY_BACKUP_DIR` | Local backup directory | `ISSUE_MEMORY_HOME/backups` |
| `ISSUE_MEMORY_WINDOWS_BACKUP_TARGET` | Optional mirrored backup path | unset |
| `ISSUE_MEMORY_CALIBRATION_PROFILE_PATH` | Saved calibration profile | `ISSUE_MEMORY_STATE_DIR/calibration_profile.json` |

## MCP process lifecycle

| Variable | Default | Purpose |
| --- | --- | --- |
| `ISSUE_MEMORY_SERVER_REQUIRE_OWNER_KEY` | runtime default `0`, recommended registration `1` | Refuse startup unless the launcher provides an owner key |
| `ISSUE_MEMORY_MAIN_CONVERSATION_KEY` | empty | Preferred launcher-facing owner key for one main conversation |
| `ISSUE_MEMORY_MAIN_CONVERSATION_KEY_ENV` | empty | Optional indirection alias for the preferred main-conversation key |
| `ISSUE_MEMORY_MCP_OWNER_KEY` | empty | Secondary compatibility direct owner key |
| `ISSUE_MEMORY_MCP_OWNER_KEY_ENV` | empty | Secondary compatibility indirection alias |
| `ISSUE_MEMORY_MAIN_CONVERSATION_ROLE` | empty | Preferred launcher-facing diagnostics label such as `main` or `subagent` |
| `ISSUE_MEMORY_SERVER_OWNER_KEY` | empty | Low-level compatibility alias for a direct owner key |
| `ISSUE_MEMORY_SERVER_OWNER_KEY_ENV` | empty | Low-level compatibility alias: name of another env var that contains the owner key |
| `ISSUE_MEMORY_SERVER_OWNER_ROLE` | empty | Low-level compatibility alias for owner-role diagnostics |
| `ISSUE_MEMORY_MCP_OWNER_ROLE` | empty | Secondary compatibility role diagnostic alias |
| `ISSUE_MEMORY_SERVER_ALLOW_SYNTHETIC_OWNER_KEY` | `0` | Allow a generated process-scoped owner key when no explicit/derived key is available; diagnostics label it as `ISSUE_MEMORY_SYNTHETIC_OWNER_KEY` |
| `ISSUE_MEMORY_MAX_MCP_INSTANCES` | compatibility fallback; recommended value `0` | Optional global ceiling for concurrently alive MCP stdio processes; `0` disables the global cap |
| `ISSUE_MEMORY_ENFORCE_SINGLE_MCP_INSTANCE` | compatibility fallback | Legacy single-instance behavior when you intentionally want one total process |
| `ISSUE_MEMORY_SERVER_LOCK_DIR` | `ISSUE_MEMORY_STATE_DIR/run` | Lock directory for owner-key dedup |
| `ISSUE_MEMORY_SERVER_DUPLICATE_EXIT_CODE` | `75` | Exit code used when a duplicate launch is rejected for an already-owned conversation |

The recommended invariant is:

- every main Codex conversation gets one stable owner key
- the first process for that owner key starts normally
- a second process for the same owner key is rejected with exit code `75`
- different owner keys may coexist without a global total cap

## Conversation-owner integration

The recommended setup is **one server per main Codex conversation** with no global total cap.

Current repository resolution order is:

1. explicit owner vars: `ISSUE_MEMORY_MAIN_CONVERSATION_KEY`, `ISSUE_MEMORY_SERVER_OWNER_KEY`, `ISSUE_MEMORY_MCP_OWNER_KEY`
2. alias vars: `ISSUE_MEMORY_MAIN_CONVERSATION_KEY_ENV`, `ISSUE_MEMORY_SERVER_OWNER_KEY_ENV`, `ISSUE_MEMORY_MCP_OWNER_KEY_ENV`
3. `CODEX_THREAD_ID` lineage root resolution
4. parent-process lineage scan (environment + session lineage inheritance)
5. recent-session inference (safe single-owner inference from newest local sessions)
6. optional synthetic fallback (`ISSUE_MEMORY_SERVER_ALLOW_SYNTHETIC_OWNER_KEY=1`)

If explicit injection is unavailable, allow Codex/launcher-derived lineage to flow through `CODEX_THREAD_ID`.

Public launch-time variables:

| Variable | Purpose |
| --- | --- |
| `ISSUE_MEMORY_MAIN_CONVERSATION_KEY` | Preferred launcher-facing owner key. All launches for one main conversation, including subagents, should use the same value. |
| `ISSUE_MEMORY_MAIN_CONVERSATION_KEY_ENV` | Optional indirection alias for the preferred main-conversation key. |
| `ISSUE_MEMORY_MAIN_CONVERSATION_ROLE` | Preferred launcher-facing role label such as `main` or `subagent`. |
| `ISSUE_MEMORY_SERVER_OWNER_KEY` | Low-level compatibility alias for a direct owner key. |
| `ISSUE_MEMORY_SERVER_OWNER_KEY_ENV` | Low-level compatibility alias for owner-key indirection. |
| `ISSUE_MEMORY_MCP_OWNER_KEY` | Secondary compatibility direct owner key. |
| `ISSUE_MEMORY_MCP_OWNER_KEY_ENV` | Secondary compatibility indirection alias. |
| `ISSUE_MEMORY_SERVER_OWNER_ROLE` | Low-level compatibility alias for role diagnostics. |
| `ISSUE_MEMORY_MCP_OWNER_ROLE` | Secondary compatibility alias for role diagnostics. |
| `ISSUE_MEMORY_SERVER_ALLOW_SYNTHETIC_OWNER_KEY` | Optional synthetic fallback switch (`0` or `1`). |

Preferred launcher contract:

- preferably inject `ISSUE_MEMORY_MAIN_CONVERSATION_KEY` into every MCP child launch
- if explicit injection is unavailable, let the runtime derive the root main-conversation key from `CODEX_THREAD_ID` session lineage / parent inference chain
- make sure subagents resolve to the same main-conversation owner key
- optionally inject `ISSUE_MEMORY_MAIN_CONVERSATION_ROLE=main|subagent` for diagnostics
- for synthetic fallback cases, the runtime derives a process-scoped fallback key as `synthetic-process-<ppid>-<pid>` and reports its source label as `ISSUE_MEMORY_SYNTHETIC_OWNER_KEY`

How the current repo behaves when a main-conversation owner key is present:

- the first launch for that owner key is allowed
- a second concurrent launch with the **same** owner key is rejected before consuming another process slot
- the rejecting process exits with `ISSUE_MEMORY_SERVER_DUPLICATE_EXIT_CODE` (default `75`)
- `issue-memory-maint server-status` exposes the active slot `owner_key` and `owner_role`
- when `CODEX_THREAD_ID` lineage is available, inferred role is `main` for root conversations and `subagent` for non-root threads; synthetic fallback roles are `anonymous`

What this repo does **not** do by itself:

- invent a reliable conversation key
- map subagents to their parent conversation automatically
- attach a new stdio client to an already-running process

So the required Codex-side contract is:

1. resolve one stable owner key per main conversation, using the same resolution chain (explicit, aliases, lineage, parent, recent sessions, optional synthetic)
2. make sure any subagent launch resolves to that same main-conversation key
3. treat duplicate exit code `75` as a **reuse / dedup signal**, not as an uncontrolled crash
4. route the subagent through the already-owned conversation MCP instead of retrying new launches

If you still want a bounded total-process ceiling for a specific deployment, you may set `ISSUE_MEMORY_MAX_MCP_INSTANCES` to a positive integer. That global cap is now a compatibility option rather than the recommended default.

## Lifecycle status fields to observe

`issue-memory-maint server-status` returns structured lifecycle state:

- aggregate fields: `running`, `active_count`, `active_slots`, `status_path`, `lock_path`, `max_instances`, `launch_count`, `enforce_single_instance`, `db_path`, `state_dir`
- additional aggregate diagnostics: `assigned_slot`, `pid`, `parent_pid`, `command`, `process_alive`
- each `active_slots[]` item includes:
  - `slot`, `pid`, `parent_pid`, `process_alive`
  - `lock_path`, `status_path`, `command`
  - `owner_key`, `owner_key_env`, `owner_role`
  - `started_at`, `initialized_at`


## Matching and decision thresholds

| Variable | Default | Purpose |
| --- | --- | --- |
| `ISSUE_MEMORY_MATCH_ACCEPT_THRESHOLD` | `0.68` | Threshold for a confident `match` |
| `ISSUE_MEMORY_MATCH_WEAK_THRESHOLD` | `0.40` | Threshold for `ambiguous` vs `abstain` |
| `ISSUE_MEMORY_AMBIGUITY_MARGIN` | `0.09` | Required score gap before accepting a clear winner |
| `ISSUE_MEMORY_SESSION_TTL_SECONDS` | `21600` | TTL for session-local acceptance and rejection memory |
| `ISSUE_MEMORY_TELEMETRY_ENABLED` | enabled | Store retrieval events and candidate snapshots |

## Dense retrieval

| Variable | Default | Purpose |
| --- | --- | --- |
| `ISSUE_MEMORY_ENABLE_DENSE_RETRIEVAL` | enabled | Turn dense retrieval on or off |
| `ISSUE_MEMORY_DENSE_EMBEDDING_DIM` | `192` | Dense embedding dimension |
| `ISSUE_MEMORY_DENSE_CANDIDATE_LIMIT` | `16` | Dense candidate shortlist size |
| `ISSUE_MEMORY_DENSE_SIMILARITY_FLOOR` | `0.12` | Minimum similarity required for a dense hit |
| `ISSUE_MEMORY_DENSE_MODEL_NAME` | `hash-ngrams-v1` | Stored embedding model label |

## Strategy overlay and live-override safety

| Variable | Default | Purpose |
| --- | --- | --- |
| `ISSUE_MEMORY_ENABLE_STRATEGY_BANDIT` | disabled by runtime default, enabled by the installer | Enable the strategy-based ranking overlay |
| `ISSUE_MEMORY_ENABLE_STRATEGY_BANDIT_SHADOW_MODE` | disabled by runtime default, enabled by the installer | Keep the strategy overlay observational-only rather than allowing live reordering |
| `ISSUE_MEMORY_STRATEGY_OVERLAY_SCALE` | `0.20` | Strategy-level contribution |
| `ISSUE_MEMORY_VARIANT_OVERLAY_SCALE` | `0.08` | Variant-level contribution |
| `ISSUE_MEMORY_SAFE_OVERRIDE_MARGIN` | `0.03` | Required margin before a live override is allowed |
| `ISSUE_MEMORY_MINIMUM_STRATEGY_EVIDENCE` | `3` | Minimum evidence required before strategy data can promote a candidate |
| `ISSUE_MEMORY_STRATEGY_HALF_LIFE_DAYS` | `75` | Decay half-life for strategy statistics |
| `ISSUE_MEMORY_VARIANT_HALF_LIFE_DAYS` | `35` | Decay half-life for variant statistics |

## Preferences and guardrails

| Variable | Default | Purpose |
| --- | --- | --- |
| `ISSUE_MEMORY_ENABLE_PREFERENCE_RULES` | enabled | Apply prompt-driven user / repo / global preference overlays |
| `ISSUE_MEMORY_PREFERENCE_OVERLAY_SCALE` | `1.0` | Preference-rule scoring multiplier |
| `ISSUE_MEMORY_MAX_PREFERENCE_ADJUSTMENT` | `0.18` | Maximum absolute preference adjustment applied to one candidate |
| `ISSUE_MEMORY_GUARDRAIL_LIMIT` | `5` | Maximum number of guardrail items returned by `issue_guardrails` |
| `ISSUE_MEMORY_DEFAULT_USER_SCOPE` | empty | Default user-scope overlay key when not supplied per call |

## Backups, metrics, retention, and review queue

| Variable | Default | Purpose |
| --- | --- | --- |
| `ISSUE_MEMORY_LOCAL_BACKUP_KEEP` | `30` | Number of local snapshots to retain |
| `ISSUE_MEMORY_MIRROR_BACKUP_KEEP` | `15` | Number of mirrored snapshots to retain |
| `ISSUE_MEMORY_TELEMETRY_RETENTION_DAYS` | `90` | Retention window for retrieval telemetry |
| `ISSUE_MEMORY_RESOLVED_REVIEW_RETENTION_DAYS` | `120` | Retention window for resolved review queue items |

`issue_metrics` summarizes the operational state of:

- retrieval and verification behavior
- preference rule hits
- strategy overlay behavior
- review queue size
- backup freshness
- calibration profile status

## Safety and redaction

| Variable | Default | Purpose |
| --- | --- | --- |
| `ISSUE_MEMORY_ENABLE_REDACTION` | enabled | Redact secrets from env JSON, notes, and verification output |
| `ISSUE_MEMORY_ENABLE_CALIBRATION_PROFILE` | enabled | Load a saved calibration profile if present |
| `ISSUE_MEMORY_ENV_JSON_MAX_CHARS` | `4000` | Max stored chars from `env_json` |
| `ISSUE_MEMORY_VERIFICATION_OUTPUT_MAX_CHARS` | `4000` | Max stored chars from verification output |
| `ISSUE_MEMORY_NOTE_MAX_CHARS` | `2000` | Max stored chars from notes |

### Calibration profile

When `ISSUE_MEMORY_ENABLE_CALIBRATION_PROFILE` is enabled, the runtime loads a saved calibration profile from `ISSUE_MEMORY_CALIBRATION_PROFILE_PATH` (default: `ISSUE_MEMORY_STATE_DIR/calibration_profile.json`).

The calibration profile stores threshold overrides computed by the `calibrate-thresholds` maintenance command. It can contain:

- error-family-specific accept/weak thresholds
- adjusted ambiguity margins
- strategy overlay tuning parameters

To generate or update a calibration profile:

```bash
issue-memory-maint calibrate-thresholds
```

The runtime applies calibration data at startup. If the profile file does not exist, the runtime uses the default thresholds from environment variables.

To inspect the current calibration state:

```bash
issue-memory-maint metrics --window-days 14
```

The metrics output includes a `calibration_profile` section showing whether a profile is loaded and its key overrides.

## Recommended public default

The current recommended default configuration is:

- one `issue_memory` MCP block in `~/.codex/config.toml`
- `ISSUE_MEMORY_SERVER_REQUIRE_OWNER_KEY = "1"`
- `ISSUE_MEMORY_SERVER_OWNER_KEY_ENV = "ISSUE_MEMORY_MAIN_CONVERSATION_KEY"`
- `ISSUE_MEMORY_MAX_MCP_INSTANCES = "0"`
- `ISSUE_MEMORY_ENFORCE_SINGLE_MCP_INSTANCE = "0"`
- `ISSUE_MEMORY_ENABLE_STRATEGY_BANDIT = "1"`
- `ISSUE_MEMORY_ENABLE_STRATEGY_BANDIT_SHADOW_MODE = "1"`
- `ISSUE_MEMORY_ENABLE_PREFERENCE_RULES = "1"`
- `ISSUE_MEMORY_ENABLE_REDACTION = "1"`
- `ISSUE_MEMORY_ENABLE_CALIBRATION_PROFILE = "1"`

## Example runtime override

```bash
export ISSUE_MEMORY_HOME="$HOME/.local/share/codex-issue-memory"
export ISSUE_MEMORY_DB_PATH="$ISSUE_MEMORY_HOME/issue_memory.sqlite3"
export ISSUE_MEMORY_STATE_DIR="$HOME/.local/state/codex-issue-memory"
export ISSUE_MEMORY_BACKUP_DIR="$HOME/.local/share/codex-issue-memory/backups"
export ISSUE_MEMORY_LOG_DIR="$ISSUE_MEMORY_STATE_DIR/log"
export ISSUE_MEMORY_SERVER_LOCK_DIR="$ISSUE_MEMORY_STATE_DIR/run"
export ISSUE_MEMORY_SERVER_DUPLICATE_EXIT_CODE=75
export ISSUE_MEMORY_SERVER_REQUIRE_OWNER_KEY=1
export ISSUE_MEMORY_SERVER_OWNER_KEY_ENV=ISSUE_MEMORY_MAIN_CONVERSATION_KEY
export ISSUE_MEMORY_MAX_MCP_INSTANCES=0
export ISSUE_MEMORY_ENABLE_STRATEGY_BANDIT=1
export ISSUE_MEMORY_ENABLE_STRATEGY_BANDIT_SHADOW_MODE=1
export ISSUE_MEMORY_ENABLE_PREFERENCE_RULES=1
export ISSUE_MEMORY_ENABLE_REDACTION=1

# Dynamic launch-time wiring from Codex:
# export ISSUE_MEMORY_MAIN_CONVERSATION_KEY="$STABLE_MAIN_CONVERSATION_KEY"
# export ISSUE_MEMORY_MAIN_CONVERSATION_ROLE="main"
```

## Path behavior

The runtime path model is intentionally cwd-independent.

That means:

- the live database path does not depend on shell cwd
- backups and logs do not depend on shell cwd
- install-time path decisions can be propagated into the live MCP block without editing Python code

For SQLite safety, keep the live database inside the Linux or WSL filesystem. Use Windows or cloud locations only as backup mirrors, not as the active writable database.
