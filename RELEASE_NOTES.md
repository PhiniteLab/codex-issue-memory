# Release 0.2.0

Second release of `codex-issue-memory` focused on documentation overhaul, safety fixes, dead code cleanup, and a comprehensive development roadmap.

## What's changed

### Safety and correctness fixes

- streaming SHA256 hash in backup verification (previously loaded entire file into memory)
- lifecycle handle cleanup via try/finally guards and configurable join timeout
- fingerprint digest length extended from 16 to 32 characters for collision safety
- corrupt dense embedding blob warning log instead of silent skip
- JSON decode error warning log instead of silent failure
- score quantization in ranker to prevent float noise from leaking into decision boundaries

### Dead code removal

- removed 5 unused storage methods (`update_pattern`, `search_patterns`, `record_feedback`, `apply_feedback_update`, `_overwrite_update_pattern_tx`)
- removed unused `sanitize_mapping()` from security module
- removed unused `decision_policy` attribute from matching module
- removed unused `enforce_single_mcp_instance` dataclass field from settings

### Documentation overhaul

- new `docs/ROADMAP.md` with 5-phase, 22-item development plan
- `docs/ARCHITECTURE.md` expanded with learning pipeline internals: posterior model, strategy bandit flow, 23 ranking features, dense retrieval mechanics, and 6 known gaps
- `docs/README.md` restructured with proper reading order and ROADMAP entry
- `skills/issue-memory-self-learning/SKILL.md` major overhaul with feedback workflow, scope discipline, session management, and guardrail/preference guidance
- improved `docs/USAGE.md`, `docs/OPERATIONS.md`, `docs/CONFIGURATION.md`, `docs/ROLLOUT.md`, `docs/DEVELOPMENT.md`

### New test coverage

- `tests/test_concurrent_safety.py` with 11 concurrent safety tests

## Validation

- `python -m pytest -q` → `132 passed`
- `python -m codex_issue_memory.maintenance smoke` → `passed`
- `python -m codex_issue_memory.maintenance e2e-mcp-reuse-harness --json` → `passed`

---

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
