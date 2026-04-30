# Documentation

This directory contains the public and contributor documentation for `codex-issue-memory`. It focuses on the MCP runtime, local SQLite persistence, installation, operations, and development workflow.

## Runtime authority

Keep these source-of-truth rules in mind while reading or editing docs:

- the live MCP registration belongs in `~/.codex/config.toml`;
- keep exactly one `[mcp_servers.issue_memory]` block unless intentionally testing alternatives;
- runtime defaults come from `src/codex_issue_memory/settings.py`;
- `.mcp.json`, `.codex-plugin/`, `templates/`, and bundled `skills/` are reference/distribution material;
- live custom plugin or skill assets belong under `~/.codex/local-plugins/**`, not inside this repo.

## Start here

- [`INSTALLATION.md`](INSTALLATION.md): setup, verification, manual registration, and path guidance
- [`CONFIGURATION.md`](CONFIGURATION.md): environment variables and runtime defaults
- [`OWNER_KEY_CONTRACT.md`](OWNER_KEY_CONTRACT.md): owner-key model for one MCP per main Codex conversation
- [`USAGE.md`](USAGE.md): MCP tools, direct Python usage, preferences, guardrails, metrics, and CLI commands
- [`OPERATIONS.md`](OPERATIONS.md): backups, restore, logs, health checks, and troubleshooting
- [`ROLLOUT.md`](ROLLOUT.md): recommended runtime posture and alternatives
- [`ARCHITECTURE.md`](ARCHITECTURE.md): modules, request flow, ranking, learning pipeline, storage, and safety controls
- [`DEVELOPMENT.md`](DEVELOPMENT.md): local setup, validation, and contributor expectations
- [`DEPENDENCIES.md`](DEPENDENCIES.md): Python and system dependencies
- [`COMPATIBILITY.md`](COMPATIBILITY.md): platform support and WSL guidance
- [`ROADMAP.md`](ROADMAP.md): planned improvements

## Additional references

- [`ORCHESTRATION_STDIO_REUSE_CHECKLIST.md`](ORCHESTRATION_STDIO_REUSE_CHECKLIST.md): checklist for proving true stdio reuse across Codex launch patterns

## Suggested reading order

1. Read the root [`README.md`](../README.md) for the project overview.
2. Use [`INSTALLATION.md`](INSTALLATION.md) and [`CONFIGURATION.md`](CONFIGURATION.md) to get a correct live setup.
3. Read [`OWNER_KEY_CONTRACT.md`](OWNER_KEY_CONTRACT.md) before changing launcher or owner-key behavior.
4. Use [`USAGE.md`](USAGE.md), [`OPERATIONS.md`](OPERATIONS.md), and [`ROLLOUT.md`](ROLLOUT.md) for day-to-day operation.
5. Read [`ARCHITECTURE.md`](ARCHITECTURE.md) and [`DEVELOPMENT.md`](DEVELOPMENT.md) before changing code.
6. Read [`ROADMAP.md`](ROADMAP.md) for future work.

## Scope

The docs describe the repository as a runtime product. Core paths are:

- `src/codex_issue_memory/`: MCP server, retrieval logic, storage, safety, services, and maintenance CLI
- `src/codex_issue_memory/sql/`: packaged SQLite migrations
- `scripts/`: registration, verification, cron, and backup helpers
- `templates/`: example config snippets
- `tests/`: regression, lifecycle, diagnostics, and benchmark coverage
- `skills/issue-memory-self-learning/`: bundled reference content for the issue-memory workflow
