# codex-issue-memory

![codex-issue-memory cover](assets/cover.png)

`codex-issue-memory` is a **local-first MCP server for Codex** with an **optional plugin-wrapper layer** for local/custom plugin workflows.
It stores reusable debugging knowledge in local SQLite, returns compact ranked matches for recurring failures, and ships operational commands for validation, review, backup, and lifecycle health.

## Product summary

- **Primary runtime:** a Python MCP server launched as `python -m codex_issue_memory.server`
- **Public MCP surface:** exactly **12 MCP tools**
- **Public maintenance surface:** `issue-memory-maint` with exactly **26 subcommands**
- **Plugin-wrapper layer:** `.codex-plugin/plugin.json` + `.mcp.json` for local/custom plugin distribution
- **Live MCP authority:** `~/.codex/config.toml` remains the only authoritative runtime registration surface

If you want the actual `issue_memory` server available in Codex, use **Mode A**.
If you want local/custom plugin metadata for self-install or manual marketplace-style flows, use **Mode B** in addition to Mode A or a manual MCP registration flow.

## Why use it

Use `codex-issue-memory` when you want to:

- find likely prior fixes from a short error snippet instead of re-debugging from scratch,
- keep validated fixes, preferences, and guardrails in a local-first store,
- reduce repeated troubleshooting drift across similar failures,
- monitor operational health with smoke, doctor, status, backup, and review commands,
- preserve a self-hosted workflow instead of depending on a hosted memory service.

It is designed for Linux/WSL-style local development and Codex-centered MCP workflows.

## Quick start

For most users, **start here**:

```bash
git clone https://github.com/PhiniteLab/codex-issue-memory.git
cd codex-issue-memory
bash install.sh
bash scripts/verify_install.sh
```

That path:
- prepares the Python environment,
- initializes the SQLite-backed runtime,
- writes the live `issue_memory` MCP registration to `~/.codex/config.toml`,
- and verifies that the install is usable.

**Choose your path:**
- **Mode A — Standard MCP install:** use this for the real MCP runtime.
- **Mode B — Plugin-wrapper install:** use this for local/custom plugin packaging metadata and self-install ergonomics.

## Install Mode A — Standard MCP install

**Use this mode when you want Codex to actually run the `issue_memory` MCP server.**

### Recommended install

```bash
git clone https://github.com/PhiniteLab/codex-issue-memory.git
cd codex-issue-memory
bash install.sh
bash scripts/verify_install.sh
```

What Mode A does:
- installs or updates the package under `INSTALL_ROOT` (default `~/infra/codex-issue-memory`),
- creates a virtualenv under `INSTALL_ROOT/.venv`,
- initializes runtime DB/state directories,
- writes or updates exactly one live `[mcp_servers.issue_memory]` block in `~/.codex/config.toml`,
- registers the env defaults used by the MCP reuse / owner-key flow.

### Quick verification

```bash
grep -n '^\[mcp_servers.issue_memory\]' ~/.codex/config.toml
issue-memory-maint smoke
issue-memory-maint doctor --mode shadow --max-instances 0
```

Expected result:
- exactly **one** `issue_memory` MCP block in `~/.codex/config.toml`
- successful smoke / doctor output

### Manual package setup

Use this if you want a manual install without the opinionated installer:

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip setuptools wheel
python -m pip install -e .[dev]
python -m codex_issue_memory.maintenance init-db
python -m codex_issue_memory.server
```

Then add a single live `[mcp_servers.issue_memory]` block to `~/.codex/config.toml`.

## Install Mode B — Plugin-wrapper install

**Use this mode when you want local/custom plugin metadata.**

This mode is **not** the live MCP registration path.
It provides wrapper/distribution files for environments that use local plugins, plugin discovery, or manual marketplace-style bridges.

```bash
mkdir -p ~/.codex/local-plugins
cd ~/.codex/local-plugins
git clone https://github.com/PhiniteLab/codex-issue-memory.git codex-issue-memory
cd codex-issue-memory
```

Wrapper files in this repo:
- `.codex-plugin/plugin.json` → plugin identity and interface metadata
- `.mcp.json` → local/remote server connection templates

### Sanity check for wrapper metadata

```bash
python3 -m json.tool .codex-plugin/plugin.json
python3 -m json.tool .mcp.json
```

### Important limitation

Mode B by itself does **not**:
- create the Python runtime,
- initialize the SQLite database,
- or register the live `issue_memory` MCP server in `~/.codex/config.toml`.

For real runtime usage, you still need **Mode A** or a manual MCP registration flow.

## Manual marketplace integration

This repository is **not** an official OpenAI Marketplace publication.

Use this path only when you need:
- self-install from GitHub,
- local/custom plugin usage under `~/.codex/local-plugins/**`, or
- a manual marketplace-style bridge in your own environment.

Reference examples live in:
- `templates/codex_issue_memory.plugin-wrapper-example.md`

Recommended rule of thumb:
- use **Mode A** for runtime,
- use **Mode B** for plugin metadata/distribution,
- do not treat `.mcp.json` as the authoritative live MCP control plane.

## First-use examples

### Public MCP surface — 12 tools

**Retrieval and inspection**
- `issue_match` — find the best matching known issue for a failure excerpt
- `issue_get` — inspect one stored issue in more detail
- `issue_search` — keyword search across stored issues
- `issue_recent` — list recent memory entries

**Write-back and feedback**
- `issue_record_resolution` — store a verified reusable fix
- `issue_feedback` — record retrieval feedback for ranking improvement

**Preferences and guardrails**
- `issue_set_preference` — save a prompt-driven preference rule
- `issue_list_preferences` — list stored preference rules
- `issue_guardrails` — return proactive prevention guidance

**Operations and review**
- `issue_metrics` — inspect operational metrics
- `issue_review_queue` — list pending or resolved review items
- `issue_review_resolve` — resolve review queue items

### Public maintenance CLI surface — 26 subcommands

**Schema and bootstrap**
- `init-db`
- `migrate-v2`
- `schema-version`

**Backups**
- `backup`
- `list-backups`
- `verify-backup`
- `restore-backup`

**Health and lifecycle**
- `smoke`
- `smoke-learning`
- `server-status`
- `runtime-diagnostics`
- `recommended-config`
- `doctor`
- `e2e-mcp-reuse-harness`

**Operations telemetry**
- `metrics`
- `export-dashboard`
- `prune-retention`

**Review queue**
- `review-queue`
- `resolve-review`

**Benchmarks and calibration**
- `benchmark-user-domains`
- `benchmark-failure-taxonomy`
- `benchmark-dense-bandit`
- `benchmark-real-world`
- `benchmark-hard-negatives`
- `benchmark-merge-stress`
- `calibrate-thresholds`

### Common CLI commands

```bash
issue-memory-maint smoke
issue-memory-maint doctor --mode shadow --max-instances 0
issue-memory-maint server-status
issue-memory-maint metrics --window-days 30
issue-memory-maint review-queue --status pending --limit 20
issue-memory-maint e2e-mcp-reuse-harness --json
```

### Library-level example

```python
from codex_issue_memory.app import IssueMemoryApp

app = IssueMemoryApp()

result = app.issue_match(
    error_text="ModuleNotFoundError: No module named requests",
    command="python worker.py",
    file_path="api/worker.py",
    project_scope="my-repo",
)

print(result["decision"])
```

```python
app.issue_record_resolution(
    title="Missing dependency in worker runtime",
    raw_error="ModuleNotFoundError: No module named requests",
    canonical_fix="Install the dependency in the same runtime environment used by the target process.",
    prevention_rule="Pin and install runtime dependencies before process startup.",
    project_scope="my-repo",
)
```

## Architecture flow

```text
Failure appears
  ↓
Normalize failure into a structured query profile
  ↓
Retrieve ranked candidates from local SQLite-backed memory
  ↓
Return one of: match / ambiguous / abstain
  ↓
Apply feedback, preferences, and guardrails where relevant
  ↓
Record validated fixes and keep operational telemetry for lifecycle checks
```

Operationally, the recommended live posture is:
- one authoritative MCP registration in `~/.codex/config.toml`,
- one stable owner key per main Codex conversation,
- duplicate launches for the same owner key exiting with code `75` so the existing conversation MCP can be reused.

## Security / guardrails

- **Single authority rule:** keep the live MCP authority in `~/.codex/config.toml`.
- Keep exactly one `[mcp_servers.issue_memory]` block unless you intentionally manage alternatives.
- Prefer Linux/WSL-local writable paths for the active SQLite database.
- Treat repo `skills/` and `templates/` as bundled reference/distribution material, not as live runtime authority.
- Verify fixes before writing them back into issue memory.
- Use project-specific scope when a failure pattern is not globally reusable.

## Validation / doctor / smoke

### Standard runtime validation

```bash
cd ~/infra/codex-issue-memory
bash scripts/verify_install.sh
issue-memory-maint smoke
issue-memory-maint doctor --mode shadow --max-instances 0
issue-memory-maint server-status
issue-memory-maint e2e-mcp-reuse-harness --json
```

### Wrapper metadata validation

```bash
python3 scripts/validate_plugin_distribution.py
```

### Helpful live checks

```bash
grep -n '^\[mcp_servers.issue_memory\]' ~/.codex/config.toml
grep -n 'ISSUE_MEMORY_SERVER_REQUIRE_OWNER_KEY = "1"' ~/.codex/config.toml
grep -n 'ISSUE_MEMORY_MAX_MCP_INSTANCES = "0"' ~/.codex/config.toml
```

## Troubleshooting

- **Codex does not show `issue_memory`**: restart Codex and confirm there is exactly one live `[mcp_servers.issue_memory]` block in `~/.codex/config.toml`.
- **Mode confusion**: if you need the real MCP runtime, use **Mode A**. Mode B only adds plugin-wrapper metadata.
- **Install problems**: retry with `SKIP_CRON_INSTALL=1 bash install.sh` and confirm Python / pip / venv access.
- **Weak retrieval quality**: provide a shorter but more meaningful error excerpt plus command, file path, and project scope.
- **DB path / permission issues**: keep the active writable DB under Linux/WSL-local storage such as `~/.local/share/codex-issue-memory`.

## Development notes

- Runtime behavior should stay backward-compatible; wrapper files are additive.
- Do not imply official OpenAI marketplace publication in docs or release notes.
- For code changes, prefer validating with `pytest` plus the repository’s smoke/doctor flows.
- Useful references:
  - [`docs/INSTALLATION.md`](docs/INSTALLATION.md)
  - [`docs/CONFIGURATION.md`](docs/CONFIGURATION.md)
  - [`docs/USAGE.md`](docs/USAGE.md)
  - [`docs/OPERATIONS.md`](docs/OPERATIONS.md)
  - [`docs/CODEX_MAIN_CONVERSATION_OWNERSHIP.md`](docs/CODEX_MAIN_CONVERSATION_OWNERSHIP.md)
  - [`docs/ORCHESTRATION_STDLIO_REUSE_CHECKLIST.md`](docs/ORCHESTRATION_STDLIO_REUSE_CHECKLIST.md)
  - [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)
  - [`docs/DEVELOPMENT.md`](docs/DEVELOPMENT.md)

## Documentation map

See [`docs/README.md`](docs/README.md) for the full documentation index and recommended reading order.

## Repository layout

```text
src/codex_issue_memory/   MCP runtime and app logic
scripts/                  install, registration, cron, and verification helpers
docs/                     public documentation
templates/                reference snippets and plugin-wrapper examples
skills/                   bundled reference content
tests/                    regression and benchmark coverage
.codex-plugin/            local/custom plugin metadata
.mcp.json                 wrapper server templates (remote + local command)
```

## Contributing

Contributions improving install clarity, retrieval quality, and operational safety are welcome.
See [`CONTRIBUTING.md`](CONTRIBUTING.md) and [`docs/DEVELOPMENT.md`](docs/DEVELOPMENT.md).

## License

MIT. See [`LICENSE`](LICENSE).
