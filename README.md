# codex-issue-memory

![codex-issue-memory cover](assets/cover.png)

Local-first issue memory for Codex.

`codex-issue-memory` is a Python MCP server that stores reusable debugging knowledge in SQLite and returns compact, ranked matches for new failures. It keeps memory local, uses structured retrieval rather than raw transcript search, and exposes both reactive debugging tools and operational controls for safe day-to-day use.

## What it does

The server turns recurring engineering failures into reusable records:

1. normalize a failure into a structured query profile
2. retrieve the best matching known issue patterns and variants
3. rank the shortlist and decide whether the result is a clear match, ambiguous, or too weak to trust
4. record verified fixes, feedback, preferences, and review decisions
5. expose guardrails, metrics, and backup/restore operations so the memory stays usable over time

## Public MCP surface

The current MCP server exposes exactly **12 MCP tools**.

### Retrieval and inspection

- `issue_match`
- `issue_get`
- `issue_search`
- `issue_recent`

### Write-back and feedback

- `issue_record_resolution`
- `issue_feedback`

### Preferences and guardrails

- `issue_set_preference`
- `issue_list_preferences`
- `issue_guardrails`

### Operations and review

- `issue_metrics`
- `issue_review_queue`
- `issue_review_resolve`

## Public maintenance CLI surface

`issue-memory-maint` currently includes **26 subcommands**:

- **Schema/data bootstrap**
  - `init-db`
  - `migrate-v2`
  - `schema-version`
- **Backups**
  - `backup`
  - `list-backups`
  - `verify-backup`
  - `restore-backup`
- **Health and lifecycle**
  - `smoke`
  - `smoke-learning`
  - `server-status`
  - `runtime-diagnostics`
  - `recommended-config`
  - `doctor`
  - `e2e-mcp-reuse-harness`
- **Operations telemetry**
  - `metrics`
  - `export-dashboard`
  - `prune-retention`
- **Review queue**
  - `review-queue`
  - `resolve-review`
- **Benchmarks and calibration**
  - `benchmark-user-domains`
  - `benchmark-failure-taxonomy`
  - `benchmark-dense-bandit`
  - `benchmark-real-world`
  - `benchmark-hard-negatives`
  - `benchmark-merge-stress`
  - `calibrate-thresholds`

## Current default behavior

The documented public contract is:

- the authoritative MCP registration lives in `~/.codex/config.toml`
- keep exactly one `[mcp_servers.issue_memory]` block there
- the live custom skill / plugin root is `~/.codex/local-plugins/**`
- bundled skill content in this repository is reference-only; if you install a live custom wrapper, keep it under `~/.codex/local-plugins/**`
- `~/.agents/plugins/**` is a generated compatibility bridge back to `~/.codex/local-plugins/**`
- the recommended runtime requires one stable owner key per main Codex conversation
- the default registration sets `ISSUE_MEMORY_SERVER_REQUIRE_OWNER_KEY = "1"`
- the default registration resolves owner key in this order:
  1. `ISSUE_MEMORY_MAIN_CONVERSATION_KEY` (preferred), `ISSUE_MEMORY_SERVER_OWNER_KEY`, `ISSUE_MEMORY_MCP_OWNER_KEY`
  2. explicit alias names (`..._KEY_ENV` forms)
  3. direct `CODEX_THREAD_ID` lineage and root derivation
  4. parent-process lineage and inherited launch environment
  5. recent-session inference
  6. optional synthetic fallback from `ISSUE_MEMORY_SERVER_ALLOW_SYNTHETIC_OWNER_KEY` (`ISSUE_MEMORY_SYNTHETIC_OWNER_KEY`)
- the default registration disables the global total cap with `ISSUE_MEMORY_MAX_MCP_INSTANCES = "0"`
- duplicate launches for the same owner key exit with code `75` so Codex can reuse the already-owned conversation MCP
- the strategy overlay is enabled, but live override is disabled by default
- legacy online/contextual learning runtime is retired; upgrade migration `009_cleanup_learning_state` removes old learning-state tables if present
- preference rules, guardrails, redaction, telemetry, calibration profiles, backups, and review-queue support are all available in the current public build

## Key capabilities

- local-first SQLite storage with packaged migrations
- lexical + dense retrieval over normalized issue memory
- variant-aware matching with explicit `match`, `ambiguous`, and `abstain` decisions
- structured write-back for verified reusable fixes
- feedback-driven reranking support and session-local memory
- prompt-driven preferences and proactive guardrails
- operational metrics, review queue management, backup verification, and restore commands
- cwd-independent path handling for the database, state, logs, and backup locations
- one-server-per-main-conversation dedup for runtimes that pass a stable owner key
- optional compatibility cap support when you explicitly choose to keep a global process ceiling

## Installation

### Recommended installed setup

```bash
git clone <repo-url>
cd codex-issue-memory
bash install.sh
```

After installation:

- verify the live registration in `~/.codex/config.toml`
- ensure there is only one `issue_memory` MCP block
- treat `~/.codex/local-plugins/**` as the live custom plugin / skill root
- treat any `.agents/plugins/**` entries as generated bridge surfaces, not bootstrap authority

### Manual package setup

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip setuptools wheel
python -m pip install -e .[dev]
python -m codex_issue_memory.maintenance init-db
python -m codex_issue_memory.server
```

Then add one `issue_memory` MCP server block to `~/.codex/config.toml`.

For full setup guidance, see [`docs/INSTALLATION.md`](docs/INSTALLATION.md).

## Quick Python example

```python
from codex_issue_memory.app import IssueMemoryApp

app = IssueMemoryApp()

result = app.issue_match(
    error_text="ModuleNotFoundError: No module named requests",
    command="python worker.py",
    file_path="api/worker.py",
    repo_name="tooling-lab",
    project_scope="tooling-lab",
)

if result["decision"] == "match":
    top = result["matches"][0]
    print(top["title"])
```

Record a verified fix:

```python
app.issue_record_resolution(
    title="Requests missing in API worker",
    raw_error="ModuleNotFoundError: No module named requests while starting API worker",
    canonical_fix="Install requests into the active environment used by the API worker.",
    prevention_rule="Pin and install runtime dependencies in the worker environment.",
    canonical_symptom="requests import fails during api worker startup",
    verification_steps="Run the worker import check in the same environment.",
    project_scope="global",
    tags="python,import,requests,api",
)
```

Set a preference rule:

```python
app.issue_set_preference(
    instruction="Prefer fixes that preserve the existing SQLite schema and avoid destructive rewrites.",
    project_scope="my-repo",
    mode="prefer",
)
```

Inspect metrics:

```python
app.issue_metrics(window_days=30)
```

## Operations summary

Common CLI commands:

```bash
issue-memory-maint smoke
issue-memory-maint metrics --window-days 14
issue-memory-maint list-backups --limit 5
issue-memory-maint verify-backup /path/to/backup.sqlite3
issue-memory-maint restore-backup /path/to/backup.sqlite3
issue-memory-maint server-status
issue-memory-maint doctor --mode shadow --max-instances 0
issue-memory-maint e2e-mcp-reuse-harness --json
```

The maintenance CLI also includes:

- schema and initialization commands
- review queue inspection and resolution
- retention pruning
- dashboard export
- calibration and benchmark commands

For duplicate-owner reuse checks, note the status payload includes:

- `active_count`, `active_slots`
- each slot’s `slot`, `pid`, `parent_pid`, `command`, `owner_key`, `owner_key_env`, `owner_role`
- aggregate fields such as `running`, `launch_count`, `status_path`, `lock_path`, `assigned_slot`, `max_instances`, `enforce_single_instance`
- configured duplicate exit code from `ISSUE_MEMORY_SERVER_DUPLICATE_EXIT_CODE` (default `75`)

See [`docs/OPERATIONS.md`](docs/OPERATIONS.md) and [`docs/USAGE.md`](docs/USAGE.md) for details.

## Documentation map

- Release-critical docs:
  - [`docs/CODEX_MAIN_CONVERSATION_OWNERSHIP.md`](docs/CODEX_MAIN_CONVERSATION_OWNERSHIP.md): owner-key chain, duplicate reuse semantics, synthetic fallback, and orchestration handoff
  - [`docs/ORCHESTRATION_STDLIO_REUSE_CHECKLIST.md`](docs/ORCHESTRATION_STDLIO_REUSE_CHECKLIST.md): proving real stdio reuse in a live launcher
  - [`docs/INSTALLATION.md`](docs/INSTALLATION.md): bootstrap, verification, and config authority
- [`docs/README.md`](docs/README.md): documentation index and reading order
- [`docs/CONFIGURATION.md`](docs/CONFIGURATION.md): runtime variables, defaults, and public config model
- [`docs/USAGE.md`](docs/USAGE.md): tool-by-tool usage and CLI command groups
- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md): internals, data model, ranking, safety, and operations
- [`docs/OPERATIONS.md`](docs/OPERATIONS.md): backups, restore, health checks, logs, and troubleshooting
- [`docs/ROLLOUT.md`](docs/ROLLOUT.md): recommended runtime posture and configuration choices
- [`docs/DEVELOPMENT.md`](docs/DEVELOPMENT.md): contributor workflow and validation guidance

## Repository layout

```text
src/codex_issue_memory/   main package
scripts/                  install, registration, cron, and verification helpers
docs/                     public documentation
templates/                example config snippets
skills/                   bundled reference content for the issue-memory skill
tests/                    regression and benchmark coverage
```

The repository ships bundled skill content for reference, but the live Codex custom plugin / skill root is `~/.codex/local-plugins/**`.
Treat `skills/issue-memory-self-learning/` as a bundled reference copy. If you package a live custom wrapper around it, place that wrapper under `~/.codex/local-plugins/**`.

## Contributing

Contributions that improve correctness, retrieval quality, installation clarity, and operational safety are welcome. See [`CONTRIBUTING.md`](CONTRIBUTING.md) and [`docs/DEVELOPMENT.md`](docs/DEVELOPMENT.md).

## License

MIT. See [`LICENSE`](LICENSE).
