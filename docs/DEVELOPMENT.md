# Development

This guide is for contributors working from a source checkout.

## Local setup

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip setuptools wheel
python -m pip install -e .[dev]
```

This gives you:

- the package in editable mode
- the MCP dependency stack
- `pytest`

The repository also includes `pyrightconfig.json`. Pyright is optional and not installed by the default development extra.

## Useful commands

Run the test suite:

```bash
python -m pytest
```

Run smoke checks:

```bash
python -m codex_issue_memory.maintenance smoke
python -m codex_issue_memory.maintenance smoke-learning
```

Inspect operations and diagnostics:

```bash
python -m codex_issue_memory.maintenance server-status
python -m codex_issue_memory.maintenance metrics --window-days 14
python -m codex_issue_memory.maintenance runtime-diagnostics
python -m codex_issue_memory.maintenance e2e-mcp-reuse-harness --json
python -m codex_issue_memory.maintenance benchmark-real-world
python -m codex_issue_memory.maintenance benchmark-hard-negatives
python -m codex_issue_memory.maintenance benchmark-merge-stress
```

Run the MCP server directly:

```bash
python -m codex_issue_memory.server
```

Show local tool help:

```bash
python -m codex_issue_memory.maintenance --help
python scripts/register_codex.py --help
python scripts/e2e_mcp_reuse_harness.py --json
```

## Repository layout

Top-level responsibilities:

- `pyproject.toml`: package metadata, console entrypoints, and pytest configuration
- `install.sh`: convenience installer for local Codex-oriented setups
- `src/codex_issue_memory/`: runtime package and maintenance CLI
- `scripts/`: setup and helper scripts
- `docs/`: public and contributor documentation
- `skills/`: bundled reference skill content shipped with the repository
- `templates/`: example config and helper templates
- `tests/`: regression, lifecycle, diagnostics, and benchmark coverage

## Source-of-truth guidance

When you touch install, configuration, or docs surfaces, keep these distinctions clear:

- the live MCP registration is the single `[mcp_servers.issue_memory]` block in `~/.codex/config.toml`
- live custom skill or plugin assets belong under `~/.codex/local-plugins/**`
- bundled skill content in this repository is reference-only; any live custom wrapper should live under `~/.codex/local-plugins/**`
- repository templates are examples
- repository `skills/` content is bundled source material, not the live Codex bootstrap state

Do not edit the bundled `skills/issue-memory-self-learning/` copy as though it were the live runtime source of truth.

## Validation expectations

For most code changes, run:

1. `python -m pytest`
2. `python -m codex_issue_memory.maintenance smoke`

Recommended additional checks when relevant:

- `python -m codex_issue_memory.maintenance smoke-learning`
- `python -m codex_issue_memory.maintenance server-status`
- `python -m codex_issue_memory.maintenance e2e-mcp-reuse-harness --json`
- `python -m codex_issue_memory.maintenance metrics --window-days 14`
- `pyright`
- targeted benchmark commands if you changed retrieval, learning, or consolidation behavior

For public-facing documentation and release-readiness updates:

- verify `docs/README.md` reading order and release-critical docs list remains consistent
- verify `README.md` and `CHANGELOG.md` reflect the same MCP/Maintenance surfaces as code
- run at least:
  - `python -m codex_issue_memory.maintenance server-status`
  - `python -m codex_issue_memory.maintenance smoke`
  - `python -m codex_issue_memory.maintenance e2e-mcp-reuse-harness --json`
- confirm the installed `~/.codex/config.toml` MCP block matches the documented launch model before publishing

If you changed installation, registration, backup, or path logic:

1. test `bash install.sh` in a temporary directory
2. inspect the generated `~/.codex/config.toml`-equivalent output in the temp Codex home
3. run the installed `scripts/verify_install.sh`
4. confirm the docs still describe the public live surfaces correctly

## Temporary install check

```bash
tmpdir="$(mktemp -d)"

INSTALL_ROOT="$tmpdir/install" DATA_ROOT="$tmpdir/data" STATE_ROOT="$tmpdir/state" CODEX_HOME="$tmpdir/codex-home" SKIP_CRON_INSTALL=1 PYTHON_BIN=python3 bash install.sh

bash "$tmpdir/install/scripts/verify_install.sh"
```


## Documentation maintenance tips

When updating public docs, verify that:

- tool counts and tool names match `src/codex_issue_memory/server.py`
- maintenance command lists match `src/codex_issue_memory/maintenance.py`
- backup and restore guidance matches `src/codex_issue_memory/backup.py`
- configuration examples still describe the single live `~/.codex/config.toml` MCP entry

## PR guidance

Good pull requests are:

- narrow in scope
- validated
- honest about tradeoffs
- careful with SQLite path behavior and Codex integration
- consistent with the public documentation

Please include:

- what changed
- why it changed
- how you validated it
- any remaining risks or follow-up work

For live launcher validation of parent/subagent MCP reuse, keep the separate orchestration checklist up to date: [`ORCHESTRATION_STDLIO_REUSE_CHECKLIST.md`](ORCHESTRATION_STDLIO_REUSE_CHECKLIST.md).

For the broader project contribution guide, see [`../CONTRIBUTING.md`](../CONTRIBUTING.md).
