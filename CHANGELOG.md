# Changelog

All notable changes to this project should be documented in this file.

The format is intentionally simple and human-readable.

## 0.1.0 (2026-03-29)

### Added

- dedicated documentation set under `docs/`
- installation, configuration, usage, operations, architecture, and development guides
- explicit compatibility and dependency documentation
- public-facing community files for contributing, support, and security reporting
- repository-tracked `RELEASE_NOTES.md` for versioned GitHub release text
- GitHub Actions release workflow for publishing the current version and release notes on push to `main`
- release-critical documentation for conversation-owner lifecycle and stdio reuse (`CODEX_MAIN_CONVERSATION_OWNERSHIP.md`, `ORCHESTRATION_STDLIO_REUSE_CHECKLIST.md`)
- documentation coverage for maintenance CLI operations including status payloads, benchmark suites, review queue workflows, and doctor/recommended-config checks
- full maintenance-command surface documentation (26 subcommands), including schema/bootstrap, backups, health diagnostics, review, runtime snapshots, and calibration/benchmark workflows
- lifecycle/operator docs for process slots and owner-key metadata (`running`, `active_count`, `active_slots`, `assigned_slot`, `status_path`, `lock_path`, `owner_key`, `owner_role`, `owner_key_env`)
- explicit owner-key and preference/guardrail documentation updates for `issue_set_preference`, `issue_list_preferences`, `issue_guardrails`, and review-resolve paths (`issue_review_queue`, `issue_review_resolve`)

### Changed

- installer and cron helper guidance now document safer shell-script invocation patterns
- public-facing docs now state Linux and WSL support more explicitly
- package metadata now includes public-facing keywords and classifiers
- removed the retired online/contextual learning runtime paths and their public configuration surface
- aligned docs for current owner-key resolution chain: explicit envs, aliases, `CODEX_THREAD_ID` lineage, parent-process lineage, recent-session inference, and synthetic fallback
- aligned docs for duplication/reuse behavior with explicit exit code `75` handling and lifecycle status visibility
- aligned docs for owner-role inference (`main`, `subagent`, `anonymous`) and repo-side reuse verification (`e2e-mcp-reuse-harness`)
- refreshed rollout/operations/installation documentation to reflect current orchestration posture and review/benchmark tooling
- generalized bundled-skill guidance so the repo no longer carries maintainer-specific plugin paths
- added upgrade coverage for the cleanup migration that drops retired learning-state tables from older installs

### Removed

- retired online/contextual learning runtime paths from active code path
