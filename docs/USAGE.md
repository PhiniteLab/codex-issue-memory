# Usage

The normal workflow is:

1. query the memory before repeating a long debugging session
2. inspect the top candidate only when the answer is ambiguous
3. apply and verify a fix
4. record the resolution only if it is reusable
5. use preferences, guardrails, metrics, and review tools to keep the memory operationally healthy

## Two ways to use the package

### Through MCP

After the server is registered in `~/.codex/config.toml`, Codex can call the `issue_memory` MCP server directly.

### Through Python

If you are working from a checkout or embedding the package directly:

```python
from codex_issue_memory.app import IssueMemoryApp

app = IssueMemoryApp()
```

The same workflows are available as methods on `IssueMemoryApp`.

## MCP tool groups

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

## Matching issues

Use `issue_match` first when you have a real failure and want the cheapest memory-first lookup.

Good inputs:

- the shortest meaningful error excerpt
- the failing command
- the most relevant file path
- the correct `project_scope`
- a stable `session_id` if you want session-local reranking

### `session_id` behavior

Pass a stable `session_id` to enable session-local memory:

- the system remembers which candidates were accepted or rejected within the session
- rejected candidates receive a penalty on subsequent queries in the same session
- this prevents the same wrong suggestion from appearing repeatedly during one debugging flow
- do not change `session_id` mid-session — this resets session-local reranking
- session memory expires after the configured TTL (`ISSUE_MEMORY_SESSION_TTL_SECONDS`, default 6 hours)

Example:

```python
result = app.issue_match(
    error_text="FileNotFoundError: config/contractsDatabase.sqlite3",
    command="python -m app.main",
    file_path="services/db_loader.py",
    repo_name="my-repo",
    project_scope="my-repo",
    session_id="debug-session-17",
    limit=3,
)
```

The result includes:

- `query_profile`
- `decision`
- `retrieval_event_id`
- compact `matches`
- `next_action`

Decision meanings:

- `match`: one candidate is clearly ahead
- `ambiguous`: compare the top one or two candidates with `issue_get`
- `abstain`: continue fresh debugging

### How retrieval works internally

`issue_match` uses a multi-source retrieval pipeline:

1. **Lexical retrieval**: SQLite FTS5 full-text search over stored patterns and variants
2. **Variant-aware retrieval**: fingerprint matching on commands, paths, stacks, repos, and environments
3. **Dense retrieval** (when enabled): character n-gram hash embeddings produce a lightweight semantic similarity signal without external model dependencies
4. **Ranking**: all candidates are scored using 23 weighted features covering scope, family, similarity, feedback history, and session memory
5. **Decision**: the ranker output is converted to `match` / `ambiguous` / `abstain` based on configured thresholds

When the strategy bandit is enabled, an optional overlay score from Thompson Sampling is applied (or logged in shadow mode) before the final decision.

## Inspecting stored memory

### `issue_get`

Fetch the full pattern bundle, including variants and episodes.

```python
bundle = app.issue_get(pattern_id=12, include_examples=True, examples_limit=5)
```

### `issue_search`

Run keyword-style search across stored memory.

```python
hits = app.issue_search(query="sqlite cwd path", project_scope="global", limit=5)
```

### `issue_recent`

Show recently updated patterns for a scope.

```python
recent = app.issue_recent(limit=5, project_scope="my-repo")
```

## Recording resolutions

Use `issue_record_resolution` only after the fix is verified and worth reusing.

Strong write-backs are:

- specific
- normalized
- reusable across future failures
- backed by clear verification steps

Avoid storing:

- one-off typos
- speculative fixes
- long raw logs
- cosmetic changes with no reusable debugging value

Example:

```python
stored = app.issue_record_resolution(
    title="Resolve SQLite path relative to module file",
    raw_error="FileNotFoundError: references/contractsDatabase.sqlite3",
    canonical_fix="Build the SQLite path from Path(__file__).resolve().parent.",
    prevention_rule="Never depend on runtime cwd for local database paths.",
    canonical_symptom="sqlite database path fails when launched from another working directory",
    verification_steps="Run the module from repo root and from an external cwd.",
    project_scope="global",
    tags="sqlite,path,cwd",
)
```

Internally, the write path stores a pattern, a context-specific variant, and an episode in one transaction.

## Submitting feedback

Use `issue_feedback` after trying a candidate. **Always provide feedback** — this is how the system learns and improves future retrieval quality.

```python
feedback = app.issue_feedback(
    retrieval_event_id=result["retrieval_event_id"],
    feedback_type="fix_verified",
    candidate_rank=1,
    notes="Verified by rerunning the failing command.",
)
```

Supported feedback types:

- `candidate_accepted`
- `candidate_rejected`
- `fix_verified`
- `false_positive`
- `merge_confirmed`
- `merge_rejected`
- `split_confirmed`
- `split_rejected`

Feedback updates:

- pattern and variant statistics
- structured telemetry
- session-local memory
- optional ranking overlays that are enabled in the current configuration

## Preferences and guardrails

### `issue_set_preference`

Store a prompt-driven preference rule without duplicating issue memory.

```python
app.issue_set_preference(
    instruction="Prefer fixes that preserve the current SQLite schema.",
    project_scope="my-repo",
    mode="prefer",
)
```

### `issue_list_preferences`

List stored preference rules.

```python
app.issue_list_preferences(project_scope="my-repo", active_only=True, limit=10)
```

### `issue_guardrails`

Request proactive prevention hints before or during debugging.

```python
app.issue_guardrails(
    error_text="FileNotFoundError: config/contractsDatabase.sqlite3",
    command="python -m app.main",
    file_path="services/db_loader.py",
    project_scope="my-repo",
)
```

Guardrails can combine stored issue patterns with explicit preference rules.

## Metrics and review queue

### `issue_metrics`

Return operational metrics for a time window.

```python
metrics = app.issue_metrics(window_days=30)
```

Metrics include data about:

- retrieval and verification activity
- preference rule hits
- strategy overlay behavior
- review queue size
- backup freshness
- calibration profile state

### `issue_review_queue`

Inspect pending or resolved review items.

```python
queue = app.issue_review_queue(status="pending", limit=20)
```

### `issue_review_resolve`

Resolve a review item.

```python
app.issue_review_resolve(review_id=7, decision="approve", note="Verified against repo-specific failure context.")
```

## Scope guidance

`project_scope` is one of the most important inputs.

Use the repository name when the issue is specific to one codebase:

```text
project_scope = "my-private-repo"
```

Use `global` only for stable cross-repo patterns such as:

- cwd-relative path bugs
- common import failures
- generic SQLite path issues
- reusable tensor device, dtype, or shape failures

If you use user-specific preference rules, pass `user_scope` consistently so the same preference set can be reused.

## Maintenance CLI

Use `issue-memory-maint --help` for the authoritative command list. The CLI is grouped by operational purpose rather than by research or publication workflows.

Installed entrypoint:

```bash
issue-memory-maint --help
```

Module form:

```bash
python -m codex_issue_memory.maintenance --help
```

### Database and schema

- `init-db`
- `migrate-v2`
- `schema-version`

### Backups and restore

- `backup`
- `list-backups`
- `verify-backup`
- `restore-backup`

### Health and lifecycle

- `smoke`
- `smoke-learning`
- `server-status`
- `runtime-diagnostics`
- `recommended-config`
- `doctor`
- `e2e-mcp-reuse-harness`

### Metrics, retention, and review

- `metrics`
- `export-dashboard`
- `prune-retention`
- `review-queue`
- `resolve-review`

### Benchmarks and calibration

These commands are runtime-quality and retrieval-quality checks for the MCP service.

- `benchmark-user-domains`
- `benchmark-failure-taxonomy`
- `benchmark-dense-bandit`
- `benchmark-real-world`
- `benchmark-hard-negatives`
- `benchmark-merge-stress`
- `calibrate-thresholds`
- `calibrate-weights`
- `analyze-feature-importance`
- `sweep-implicit`

### Experiment registry

- `create-experiment`
- `update-experiment`
- `analyze-experiment`

Examples:

```bash
issue-memory-maint smoke
issue-memory-maint metrics --window-days 14
issue-memory-maint list-backups --limit 5
issue-memory-maint verify-backup /path/to/backup.sqlite3
issue-memory-maint restore-backup /path/to/backup.sqlite3
issue-memory-maint doctor --mode shadow --max-instances 0
issue-memory-maint e2e-mcp-reuse-harness --json
issue-memory-maint review-queue --status pending --limit 20
```

## Main-conversation ownership behavior

For owner-key-aware deployments, `issue-memory` exposes controlled stdio reuse through
`MCPServerLifecycle`:

1. resolve owner identity using the full chain:
   - direct owner vars (`ISSUE_MEMORY_MAIN_CONVERSATION_KEY`, `ISSUE_MEMORY_SERVER_OWNER_KEY`, `ISSUE_MEMORY_MCP_OWNER_KEY`)
   - alias vars (`..._KEY_ENV`)
   - `CODEX_THREAD_ID` lineage
   - parent-process lineage
   - recent session inference
   - optional synthetic fallback (`ISSUE_MEMORY_SERVER_ALLOW_SYNTHETIC_OWNER_KEY`)
2. infer owner role:
   - explicit role vars win first (`ISSUE_MEMORY_MAIN_CONVERSATION_ROLE`, `ISSUE_MEMORY_SERVER_OWNER_ROLE`, `ISSUE_MEMORY_MCP_OWNER_ROLE`)
   - `main` when the current thread equals the resolved owner thread id
   - `subagent` when it does not
   - `anonymous` for synthetic fallback keys
3. reject duplicate same-owner launches with configured exit code (default `75`)
4. emit owner diagnostics via `server-status` (`owner_key`, `owner_role`, `owner_key_env`)

Use this behavior in combination with the orchestration checklist:

- repo-side: [`ORCHESTRATION_STDIO_REUSE_CHECKLIST.md`](ORCHESTRATION_STDIO_REUSE_CHECKLIST.md) (entrypoint verification)
- duplicate exit handling: treat exit `75` as a reuse signal, not a startup failure

## Server status and slot visibility

`issue-memory-maint server-status` is the runtime source of truth for lifecycle:

- aggregate fields:
  - `running`, `pid`, `parent_pid`, `assigned_slot`
  - `active_count`, `active_slots`, `launch_count`, `process_alive`
  - `max_instances`, `lock_acquired`, `enforce_single_instance`
  - `status_path`, `lock_path`, `db_path`, `state_dir`
  - `owner_key`, `owner_key_env`, `owner_role` (for the first slot when present)
- each `active_slots[]` entry includes at minimum:
  - `slot`, `pid`, `parent_pid`, `command`
  - `owner_key`, `owner_key_env`, `owner_role`
  - `started_at`, `initialized_at`, `process_alive`
  - `lock_path`, `status_path`

Use this in pre-production checks before changing rollout posture.

## Running the server directly

From a checkout:

```bash
PYTHONPATH=src python3 -m codex_issue_memory.server
```

From an installed environment:

```bash
python -m codex_issue_memory.server
```
