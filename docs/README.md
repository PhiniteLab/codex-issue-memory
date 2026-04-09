# Documentation

This directory expands on the root [`README.md`](../README.md) with setup, configuration, usage, operations, architecture, learning internals, and contributor guidance.

## Authoritative public surfaces

For public users, read the documentation with these rules in mind:

- the authoritative MCP registration lives in `~/.codex/config.toml`
- keep exactly one `[mcp_servers.issue_memory]` block there
- the live custom plugin / skill root is `~/.codex/local-plugins/**`
- bundled skill content in this repository is reference-only; any live custom wrapper should live under `~/.codex/local-plugins/**`
- example files in `templates/` are reference snippets only
- runtime defaults come from `src/codex_issue_memory/settings.py`

## Start here

- [`INSTALLATION.md`](INSTALLATION.md): setup, verification, manual registration, and path guidance
- [`CONFIGURATION.md`](CONFIGURATION.md): runtime variables, defaults, and public configuration model
- [`CODEX_MAIN_CONVERSATION_OWNERSHIP.md`](CODEX_MAIN_CONVERSATION_OWNERSHIP.md): owner-key contract for one MCP per main Codex conversation
- [`USAGE.md`](USAGE.md): MCP tools, direct Python usage, preferences, guardrails, metrics, and CLI commands
- [`OPERATIONS.md`](OPERATIONS.md): backups, restore, logs, health checks, and troubleshooting
- [`ROLLOUT.md`](ROLLOUT.md): recommended default runtime posture and alternative configuration choices
- [`ARCHITECTURE.md`](ARCHITECTURE.md): module responsibilities, request flow, ranking, learning pipeline, storage, and safety controls
- [`DEVELOPMENT.md`](DEVELOPMENT.md): local setup, validation, and contributor expectations
- [`DEPENDENCIES.md`](DEPENDENCIES.md): Python and system dependencies
- [`COMPATIBILITY.md`](COMPATIBILITY.md): platform support and WSL guidance
- [`ROADMAP.md`](ROADMAP.md): development plan and improvement roadmap

### Additional references

- [`ORCHESTRATION_STDLIO_REUSE_CHECKLIST.md`](ORCHESTRATION_STDLIO_REUSE_CHECKLIST.md): live launcher checklist for proving true stdio reuse

### Release-critical order for publication

For public release readers:

1. [`INSTALLATION.md`](INSTALLATION.md)
2. [`CONFIGURATION.md`](CONFIGURATION.md)
3. [`CODEX_MAIN_CONVERSATION_OWNERSHIP.md`](CODEX_MAIN_CONVERSATION_OWNERSHIP.md)
4. [`USAGE.md`](USAGE.md)
5. [`OPERATIONS.md`](OPERATIONS.md)
6. [`ROLLOUT.md`](ROLLOUT.md)

## Suggested reading order

1. Read the root [`README.md`](../README.md) for the project overview.
2. Use [`INSTALLATION.md`](INSTALLATION.md) and [`CONFIGURATION.md`](CONFIGURATION.md) to get a correct live setup.
3. Read [`CODEX_MAIN_CONVERSATION_OWNERSHIP.md`](CODEX_MAIN_CONVERSATION_OWNERSHIP.md) to understand the owner-key contract.
4. Use [`USAGE.md`](USAGE.md) and [`OPERATIONS.md`](OPERATIONS.md) for day-to-day usage.
5. Use [`ROLLOUT.md`](ROLLOUT.md) to choose a configuration posture.
6. Read [`ARCHITECTURE.md`](ARCHITECTURE.md) and [`DEVELOPMENT.md`](DEVELOPMENT.md) before changing code.
7. Read [`ROADMAP.md`](ROADMAP.md) for planned improvements and learning pipeline evolution.

## Scope

These docs are grounded in the repository as it exists today:

- `src/codex_issue_memory/` for the MCP server, retrieval logic, storage, safety, and maintenance CLI
- `scripts/` for registration, verification, cron, and backup helpers
- `templates/` for example config snippets
- `tests/` for regression and benchmark coverage
- `skills/issue-memory-self-learning/` for bundled reference content related to the issue-memory workflow; any live custom wrapper belongs under `~/.codex/local-plugins/**`
