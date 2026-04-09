# Architecture

`codex-issue-memory` is a local-first MCP service that turns recurring debugging knowledge into a structured, queryable memory.

At a high level, the service:

1. normalizes a failure into a query profile
2. retrieves likely pattern and variant matches
3. ranks candidates with conservative overlays
4. exposes preventive controls such as preferences and guardrails
5. stores verified reusable fixes
6. records feedback, metrics, and review operations

## Runtime module map

| Path | Responsibility |
| --- | --- |
| `src/codex_issue_memory/server.py` | MCP server and public tool registration |
| `src/codex_issue_memory/app.py` | High-level application facade behind the MCP tools |
| `src/codex_issue_memory/settings.py` | Environment-driven settings and directory creation |
| `src/codex_issue_memory/lifecycle.py` | MCP process lifecycle tracking, owner-key dedup, and optional compatibility caps |
| `src/codex_issue_memory/storage.py` | SQLite persistence, retrieval telemetry, review queue, and metrics |
| `src/codex_issue_memory/migrations.py` | SQL migration discovery and schema application |
| `src/codex_issue_memory/backup.py` | Safe SQLite backup, verification, and restore logic |
| `src/codex_issue_memory/maintenance.py` | Maintenance CLI for setup, health, metrics, backups, review, and benchmarks |
| `src/codex_issue_memory/security.py` | Sanitization and redaction helpers |
| `src/codex_issue_memory/normalization/` | Query normalization, classification hints, fingerprints, signatures, and strategy extraction |
| `src/codex_issue_memory/retrieval/` | Candidate retrieval, ranking, and match decisions |
| `src/codex_issue_memory/services/` | Write path, feedback, preferences, guardrails, consolidation, and session workflows |
| `src/codex_issue_memory/learning/` | Current strategy-overlay logic, posteriors, and safe-override policy |
| `src/codex_issue_memory/benchmarks/` | Diagnostics, evaluation helpers, and calibration support |

## Public API surface

The MCP server registers these public tool families:

- **retrieval**: `issue_match`, `issue_get`, `issue_search`, `issue_recent`
- **write-back**: `issue_record_resolution`, `issue_feedback`
- **preferences and prevention**: `issue_set_preference`, `issue_list_preferences`, `issue_guardrails`
- **operations**: `issue_metrics`, `issue_review_queue`, `issue_review_resolve`

`IssueMemoryApp` mirrors the same workflows for direct Python usage.

## Data model

The storage model separates stable reusable knowledge, context-specific reuse, operational metadata, and review state.

### Core memory records

- `issue_patterns`: canonical reusable issue records
- `issue_variants`: context-specific variants for paths, commands, environments, stacks, and repositories
- `issue_episodes`: concrete recorded incidents attached to patterns and variants
- `issue_examples`: example rows kept for retrieval support and compatibility

### Retrieval, feedback, and session state

- `retrieval_events`: one row per match or search request
- `retrieval_candidates`: ranked candidate snapshots with scores, reasons, and feature values
- `feedback_events`: structured outcomes for retrieval attempts
- `session_memory`: short-term acceptance and rejection memory scoped to a session
- `embeddings`: stored dense vectors for patterns and variants

### Preference and review state

- `preference_rules`: user, repo, or global ranking preferences
- `review_queue`: conservative consolidation items that need an explicit decision

### Learning and support tables

- metadata, migrations, and FTS support tables

## Request flow

### 1. Normalize the input

`build_query_profile()` turns free-form debugging context into a structured profile built from:

- `error_text`
- `context`
- `command`
- `file_path`
- `stack_excerpt`
- `env_json`
- repository metadata such as `repo_name` and `git_commit`

The profile includes normalized text, exception types, inferred families, root-cause hints, tags, and stable fingerprints for commands, paths, stacks, repositories, and environments.

### 2. Retrieve candidates

`CandidateRetriever` merges several sources:

- lexical retrieval from SQLite FTS and taxonomy shortcuts
- variant-aware retrieval using command, path, stack, repo, and environment fingerprints
- fallback pattern retrieval when variant evidence is weak
- optional dense retrieval through a local hashed embedding index

Retrieval is scope-aware, so repo-specific memory can coexist with global memory.

### 3. Rank and decide

`HeuristicRanker` scores candidates using scope alignment, family and root-cause alignment, lexical overlap, dense similarity, command/path/environment similarity, feedback history, and session memory.

On top of the base score, the runtime can apply:

- preference overlays from stored preference rules
- strategy overlays from the strategy bandit
- a safe-override gate before any live strategy promotion is allowed

`MatchDecisionPolicy` then converts the shortlist into `match`, `ambiguous`, or `abstain`.

### 4. Surface proactive controls

`GuardrailService` and `PreferenceService` expose preventive behavior before or alongside reactive retrieval:

- `issue_set_preference` stores reusable ranking preferences
- `issue_list_preferences` lists stored rules
- `issue_guardrails` synthesizes proactive prevention hints, preferred strategies, and known risk patterns

These surfaces make the system useful even when no single confident stored match exists.

### 5. Store verified fixes

`RecordResolutionService` handles `issue_record_resolution`.

The write flow:

1. sanitizes sensitive fields through the security layer when redaction is enabled
2. normalizes the incident into a query profile
3. builds stable pattern and variant signatures
4. asks the consolidation service whether to create, merge, or hold a change for review
5. writes pattern, variant, episode, and support records atomically
6. refreshes embeddings when dense retrieval is enabled

When a consolidation decision is intentionally conservative, the service can place the item into the review queue instead of silently applying a risky merge.

### 6. Learn from outcomes

`FeedbackService` handles `issue_feedback`.

It:

1. resolves the referenced retrieval candidate
2. records structured feedback
3. updates pattern and variant success or rejection state
4. updates session-local memory
5. feeds any enabled learning overlays and strategy statistics

This lets later retrievals reflect verified outcomes without requiring a redesign of the base heuristic stack.

## Safety and operations

### Process lifecycle

`MCPServerLifecycle` tracks active stdio processes, writes slot status files, and enforces one-live-process-per-owner-key when the launcher supplies a stable conversation owner key.

When the runtime resolves a main-conversation owner key, preferably via explicit `ISSUE_MEMORY_MAIN_CONVERSATION_KEY` injection and otherwise through the full runtime fallback chain (`..._ENV` aliases, `CODEX_THREAD_ID` lineage, parent-process lineage, recent sessions, optional synthetic fallback), the same lifecycle layer performs owner-key dedup:

- the first process for an owner key claims the conversation
- later launches for the same owner key are rejected before taking another slot
- duplicate launches return the configured duplicate exit code so the caller can reuse the already-owned conversation server

This is intentionally only a **half implementation** inside the repo. The missing half lives in Codex orchestration:

- Codex must resolve a stable owner key per main conversation, either explicitly or from session lineage
- subagents must inherit that same key
- Codex must interpret the duplicate exit as “reuse existing conversation-owned MCP” rather than “launch another one”

The same lifecycle code still supports an optional global total cap for deployments that want a compatibility ceiling, but that cap is no longer the recommended primary control.

`server-status` is the external status surface for this layer and includes:

- aggregate lifecycle state (`running`, `active_count`, `active_slots`, `launch_count`, `max_instances`)
- per-slot identity (`slot`, `pid`, `parent_pid`, `command`, `owner_key`, `owner_key_env`, `owner_role`)
- timestamps (`started_at`, `initialized_at`) and lock/slot metadata

### Redaction

The security layer sanitizes raw error text, env payloads, notes, and verification output before storing them when redaction is enabled.

### Metrics

`storage.metrics_summary()` provides operational summaries for retrieval decisions, feedback, preference hits, strategy signals, review backlog, backup freshness, and calibration state.

### Backup and restore

`BackupManager` uses the SQLite backup API, supports backup verification, and restores backups with an optional safety-backup step.

### Calibration and diagnostics

The maintenance CLI can compute threshold calibration reports, persist calibration profiles, export dashboard snapshots, and run retrieval-oriented diagnostics.

### Retired learning-state cleanup

The current runtime no longer uses the older online-ranker or contextual-bandit state.
For upgrade safety, the packaged migrations include a cleanup step that drops those retired
tables when an older installation is migrated forward. That cleanup is intentionally
destructive for the retired learning-state tables only; the active strategy-bandit,
preference, review, and retrieval data remain intact.

## Learning pipeline internals

The learning subsystem uses hierarchical Bayesian Thompson Sampling to decide how much trust to place in each strategy and variant.

### Posterior model

Each strategy maintains a Beta posterior `(alpha, beta)` updated on feedback:
- `fix_verified` → strong positive evidence (reward 1.0)
- `false_positive` → strong negative evidence (reward -1.0)
- Posteriors decay over time with configurable half-life (`strategy_half_life_days=75`, `variant_half_life_days=35`)

Posteriors exist at three scopes: global, per-repository, and per-user. The strategy bandit samples from the posterior to produce an overlay score.

### Strategy bandit flow

1. **Observation:** For each retrieval candidate, extract the inferred strategy from normalization class hints
2. **Sample:** Draw a Thompson sample from the strategy's posterior
3. **Score overlay:** Compute `strategy_overlay = sample × strategy_overlay_scale` and `variant_overlay = variant_sample × variant_overlay_scale`
4. **Safe override gate:** Before any live promotion, verify the override margin exceeds the safety threshold and minimum evidence is met
5. **Shadow mode:** When shadow mode is enabled (default), overlays are logged but not applied to live rankings

### Ranking feature set

`HeuristicRanker` computes 23 weighted features per candidate:

| Feature group | Features | Impact |
|---|---|---|
| Scope alignment | `scope_match`, `user_scope_match` | High — filters repo-specific vs global |
| Family/class alignment | `family_match`, `root_cause_class_match`, `class_hint_match` | High — ensures semantic match |
| Lexical similarity | `lexical_score` | High — FTS-based overlap |
| Dense similarity | `dense_score` | Medium — n-gram hash embedding |
| Fingerprint matching | `command_match`, `path_match`, `env_match`, `stack_match`, `repo_match` | Medium — contextual signals |
| Feedback history | `feedback_bonus`, `variant_feedback_bonus`, `feedback_penalty` | High — learning from outcomes |
| Session memory | `session_penalty` | Medium — recent rejection dampening |
| Support evidence | `support_score` | Low (currently underweighted at 0.02) |
| Entity overlap | `entity_overlap`, `entity_conflict_penalty` | Medium — structural match |

### Dense retrieval

The optional dense retrieval subsystem uses character n-gram hashing (not neural embeddings) to produce fixed-dimension vectors (`ISSUE_MEMORY_DENSE_EMBEDDING_DIM=192`). This provides a lightweight semantic similarity signal without external model dependencies.

Dense candidates are merged with lexical FTS candidates before ranking.

### Known learning gaps

These are documented limitations being addressed in the [roadmap](ROADMAP.md):

1. **Incomplete feedback routing:** `candidate_accepted` and `candidate_rejected` update session memory but do not update variant success/reject statistics
2. **Static ranking weights:** The 23 feature weights are fixed defaults; no automatic calibration from feedback data
3. **Single-factor posteriors:** Each strategy tracks only one success/trial dimension — no separate quality, safety, and adoption factors
4. **Uniform thresholds:** `match_accept_threshold`, `match_weak_threshold`, and `ambiguity_margin` are the same across all error families
5. **No implicit feedback:** Silent rejections (user ignores a suggestion) are not captured
6. **No cross-session learning:** A pattern rejected in session A may be re-suggested in session B

## Runtime path model

`Settings.from_env()` is the runtime source of truth for filesystem paths and operational flags.

Important consequences:

- the service is cwd-independent for database, state, log, and backup paths
- live configuration is controlled by the environment values attached to the Codex MCP registration
- Codex integration is authoritative only through the single live `~/.codex/config.toml` entry
