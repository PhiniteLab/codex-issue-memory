# Release 0.1.0

Initial public release of `codex-issue-memory`.

## What's changed

### Public repository cleanup

- generalized public-facing docs, setup notes, and examples for public GitHub sharing
- removed maintainer-specific path references from public surfaces
- aligned README and docs around the current supported runtime and installation flow

### Legacy model cleanup

- removed retired online/contextual learning runtime artifacts from the active code path
- removed obsolete configuration surfaces tied to the previous model transition
- kept only the migration cleanup needed to upgrade older databases safely

### Runtime and operational surface

- preserved the current minimal working plane:
  - structured issue retrieval
  - dense retrieval
  - strategy-bandit adaptation
  - owner-key lifecycle and MCP reuse flow
- aligned maintenance and diagnostics documentation with the current runtime

### Documentation refresh

- updated README and docs for public release readiness
- clarified the public MCP tool surface and maintenance CLI surface
- corrected the README MCP tool count to match the actual server surface

## Validation

Verified in the repository:

- `./.venv/bin/python -m pytest -q` → `113 passed`
- `./.venv/bin/python -m codex_issue_memory.maintenance smoke` → `passed`
- `./.venv/bin/python -m codex_issue_memory.maintenance e2e-mcp-reuse-harness --json` → `passed`

## Notes

- legacy learning-state table names remain only where required for upgrade cleanup and migration tests
- this keeps upgrade safety intact while removing retired behavior from the active runtime and public-facing docs
