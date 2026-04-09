---
name: issue-memory-self-learning
description: Use this skill when a command, test, script, training run, or application execution fails and you want to first check for a similar known issue, then store a verified reusable fix back into the issue-memory MCP server with minimal token usage.
---

> Reference-only note: this repository copy is bundled skill content for documentation and distribution. If you install a live custom wrapper around it, keep that wrapper under `~/.codex/local-plugins/**`.

# Purpose

Use the issue-memory MCP server as a compact lessons-learned layer.

This skill is appropriate when:

- a command fails
- a stack trace appears
- a test fails
- a recurring path/config/import/database/tensor issue is likely
- a fix was verified and should be saved for later reuse
- you want to check whether this error has been seen and solved before

This skill is not for:

- dumping entire transcripts into memory
- saving unverified guesses
- saving one-off typos or trivial formatting edits
- storing cosmetic changes with no reusable debugging value

# Retrieval workflow

1. Identify the shortest meaningful failing excerpt.
   - Prefer the actual exception line plus one or two high-signal context lines.
   - Prefer the failing command and relevant file path if known.
   - Strip boilerplate stack frames — keep only the diagnostic core.

2. Call `issue_match` first.
   - Include:
     - `error_text` — shortest meaningful error excerpt
     - `command` — the failing command
     - `file_path` — most relevant file
     - `project_scope` — repository name (or `"global"` only for cross-repo patterns)
     - `session_id` — pass a stable session identifier to enable session-local reranking
   - Use `project_scope="global"` only for genuinely cross-repo reusable issues (e.g., cwd-relative path bugs, common import failures, generic SQLite path issues).
   - Otherwise use the repository name.

3. Read only the top one or two compact matches first.
   - Do not call `issue_get` unless:
     - the top scores are close,
     - the result is ambiguous,
     - or you need the full examples / verification steps.

4. If a likely fix exists, apply it and verify it with the failing command or test.

# Write-back workflow

After a fix is verified:

1. Decide whether it is memory-worthy.
   Write it back only if it is:
   - reusable — would help in future similar failures
   - recurring — has happened before or is likely to happen again
   - a stable prevention rule — teaches something about avoiding the failure class

2. Call `issue_record_resolution` with compact canonical fields:
   - `title` — short descriptive title
   - `raw_error` — the original error text (keep it short)
   - `canonical_symptom` — one-line normalized symptom description
   - `canonical_fix` — one-line actionable fix instruction
   - `prevention_rule` — how to avoid this class of failure in the future
   - `verification_steps` — how to verify the fix works
   - `project_scope` — same scope discipline as retrieval
   - `tags` — comma-separated keywords for FTS retrieval
   - optionally `error_family` and `root_cause_class` if you are confident

3. Keep the write normalized.
   Good:
   - "Resolve SQLite path relative to module file instead of cwd."
   - "Pin runtime dependencies before process startup."
   Bad:
   - "I changed a bunch of paths and it works now."
   - "Fixed the error by trying different things."

# Feedback workflow

Always provide feedback after using a retrieval result. This is how the system learns.

1. After applying a candidate fix from `issue_match`:
   - If the fix works: call `issue_feedback` with `feedback_type="fix_verified"` and `candidate_rank=1`
   - If the fix was a false positive: call `issue_feedback` with `feedback_type="false_positive"`
   - If you accept the candidate without full verification: use `feedback_type="candidate_accepted"`
   - If you reject the candidate: use `feedback_type="candidate_rejected"`

2. Always pass the `retrieval_event_id` from the `issue_match` response.

3. Include brief notes explaining the outcome — this improves future ranking.

# Preferences and guardrails

Use preferences and guardrails to shape retrieval behavior proactively:

1. **Before debugging:** call `issue_guardrails` with the error context to get prevention hints. This can surface known risk patterns even when no direct match exists.

2. **Recurring team conventions:** use `issue_set_preference` to store ranking preferences:
   - `mode="prefer"` — boost candidates matching this instruction
   - `mode="avoid"` — penalize candidates matching this instruction
   - Use `project_scope` to limit preferences to one repo, or `"global"` for universal rules.

3. **Check existing preferences:** call `issue_list_preferences` before adding duplicates.

# Scope discipline

Scope is one of the most important inputs. Getting it wrong bloats global memory or hides repo-specific patterns.

| Scope | When to use | Examples |
|---|---|---|
| `"my-repo-name"` | Issue is specific to one codebase | Internal config path, repo-specific import, test fixture issue |
| `"global"` | Issue is universally reusable | cwd-relative path bugs, common import failures, generic dtype mismatches |

When in doubt, use the repository name. It is safer to scope too narrowly than too broadly.

# Session management

- Pass a stable `session_id` to `issue_match` calls within the same debugging session.
- The system uses session memory to avoid re-suggesting rejected candidates within the same session.
- Do not change `session_id` mid-session — this resets session-local reranking.

# Token discipline

- Prefer short error excerpts over full logs.
- Prefer `issue_match` over `issue_get`.
- Prefer canonical summaries over narrative paragraphs.
- Keep project-specific issues in project scope to avoid bloating global memory.
- Use `limit=3` for initial retrieval; only increase if the first pass is ambiguous.

# Expected outcome

The memory grows as a curated issue-pattern store:
- retrieval first,
- feedback always,
- verified write-back second,
- compact outputs throughout,
- scope-disciplined entries that stay organizationally clean.
