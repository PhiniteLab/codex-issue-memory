# Changelog

All notable changes to this project should be documented in this file.

The format is intentionally simple and human-readable.

## 0.2.0 (2025-07-21)

### Added

- **Phase 1.1 — Family-specific thresholds**: `run_feedback_driven_calibration()` in `benchmarks/calibration.py` computes per-family accept/weak thresholds from real feedback data using grid search; `calibrate-thresholds --from-feedback` CLI flag; results include global and per-family optimal thresholds
- **Phase 1.2 — Contextual half-life**: `velocity_multiplier` parameter in `decay_beta_parameters()` and `build_beta_posterior()` adjusts effective half-life based on repo feedback velocity; `query_repo_feedback_velocity()` in storage computes feedback count / baseline_rate (clamped 0.5–3.0); strategy bandit passes velocity through all posterior computations
- **Phase 1.3 — Family-specific ranking weights**: `HeuristicRanker` loads `weight_overrides` from `calibration_profile.json` at initialization; `_weights_for_family()` merges default weights with per-family overrides; `score()` accepts `error_family` parameter; `rank()` extracts error_family from QueryProfile
- **Phase 1.4 — Asymmetric FP cost**: `false_positive` reward changed from −1.0 to −2.5; `FP_SAFETY_BLOCK_THRESHOLD=2` constant in `safe_override.py`; `_candidate_fp_count()` method and FP safety gate in `SafeOverridePolicy.choose()`; `fp_count` field on `StrategyBanditOutcome`
- `tests/test_phase1_improvements.py`: 27 tests covering all Phase 1 items (6 FP cost, 7 half-life, 4 calibration, 8 weights, 1 bandit, 1 CLI)
- **Phase 0.1 — Feedback loop closure**: weak feedback types (`candidate_accepted`, `candidate_rejected`, `merge_confirmed`, `merge_rejected`, `split_confirmed`, `split_rejected`) now update variant statistics with fractional weights (0.25–0.40), closing ~60% of previously lost feedback signal
- **Phase 0.2 — `proven_score` feature**: new Laplace-smoothed ranking feature `(success+1)/(used+2)` with weight 0.08; `support_score` weight raised from 0.02 to 0.05; "proven-variant" reason tag
- **Phase 0.3 — Feature-outcome log**: new `feature_outcome_log` table (migration 010), `log_feature_outcomes()` / `query_feature_outcome_stats()` storage methods, automatic feature-outcome logging in feedback pipeline, `analyze-feature-importance` CLI command
- `tests/test_phase0_improvements.py`: 15 tests covering all Phase 0 items
- `docs/ROADMAP.md`: comprehensive development plan with 5 phases and 22 improvement items
- learning pipeline internals section in `docs/ARCHITECTURE.md` covering posterior model, strategy bandit flow, 23 ranking features, and dense retrieval
- known learning gaps section in `docs/ARCHITECTURE.md` documenting 6 key limitations
- ROADMAP link in `docs/README.md` reading order and start-here list
- calibration profile explanation in `docs/CONFIGURATION.md`
- `session_id` usage guidance and dense retrieval explanation in `docs/USAGE.md`
- owner-key troubleshooting entry in `docs/OPERATIONS.md`
- `docs/ROLLOUT.md` pre-promotion readiness checklist and monitoring additions
- feedback workflow section and scope discipline guidance in `skills/issue-memory-self-learning/SKILL.md`
- concurrent safety test suite (`tests/test_concurrent_safety.py`, 11 tests)

### Changed

- `docs/README.md`: restructured reading order, added owner-key step (#3), moved orchestration checklist to additional references
- `docs/ARCHITECTURE.md`: expanded from module map to include full learning pipeline documentation
- `docs/USAGE.md`: improved `session_id` documentation and dense retrieval visibility
- `docs/OPERATIONS.md`: added owner-key troubleshooting scenario
- `docs/CONFIGURATION.md`: added calibration profile usage explanation
- `docs/ROLLOUT.md`: added transition readiness checklist and dashboard monitoring guidance
- `docs/DEVELOPMENT.md`: added roadmap and learning pipeline to contributor path
- `skills/issue-memory-self-learning/SKILL.md`: major overhaul — added feedback workflow, scope discipline, session_id guidance, and guardrail/preference workflow
- root `README.md`: updated test count (132), added roadmap doc reference
- `RELEASE_NOTES.md`: v0.2.0 release notes

### Fixed

- streaming SHA256 hash in backup verification (P0 — memory safety)
- lifecycle handle cleanup via try/finally guards and join timeout (P0 — resource leak)
- fingerprint digest length 16→32 (P1 — collision reduction)
- corrupt dense blob warning log instead of silent skip (P2)
- JSON decode error warning log instead of silent failure (P2)
- score quantization in ranker to prevent float noise leaking into decisions (P3)

### Removed

- dead code: `sanitize_mapping()` from security module
- dead code: `update_pattern()`, `search_patterns()`, `record_feedback()`, `apply_feedback_update()` from storage
- dead code: unused `decision_policy` attribute from matching
- dead code: unused `enforce_single_mcp_instance` field from settings

## 0.1.0 (2026-03-29)

### Added

- dedicated documentation set under `docs/`
- installation, configuration, usage, operations, architecture, and development guides
- explicit compatibility and dependency documentation
- public-facing community files for contributing, support, and security reporting
- repository-tracked `RELEASE_NOTES.md` for versioned GitHub release text
- GitHub Actions release workflow for publishing the current version and release notes on push to `main`
- release-critical documentation for conversation-owner lifecycle and stdio reuse (`OWNER_KEY_CONTRACT.md`, `ORCHESTRATION_STDIO_REUSE_CHECKLIST.md`)
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
