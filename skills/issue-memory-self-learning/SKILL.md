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

This skill is not for:

- dumping entire transcripts into memory
- saving unverified guesses
- saving one-off typos or trivial formatting edits

# Retrieval workflow

1. Identify the shortest meaningful failing excerpt.
   - Prefer the actual exception line plus one or two high-signal context lines.
   - Prefer the failing command and relevant file path if known.

2. Call `issue_match` first.
   - Include:
     - `error_text`
     - `command`
     - `file_path`
     - `project_scope`
   - Use `project_scope="global"` only for genuinely cross-repo reusable issues.
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
   - reusable,
   - recurring,
   - or a stable prevention rule.

2. Call `issue_record_resolution` with compact canonical fields:
   - `title`
   - `raw_error`
   - `canonical_symptom`
   - `canonical_fix`
   - `prevention_rule`
   - `verification_steps`
   - `project_scope`
   - `tags`
   - optionally `error_family` and `root_cause_class` if you are sure

3. Keep the write normalized.
   Good:
   - “Resolve SQLite path relative to module file instead of cwd.”
   Bad:
   - “I changed a bunch of paths and it works now.”

# Token discipline

- Prefer short error excerpts over full logs.
- Prefer `issue_match` over `issue_get`.
- Prefer canonical summaries over narrative paragraphs.
- Keep project-specific issues in project scope to avoid bloating global memory.

# Expected outcome

The memory grows as a curated issue-pattern store:
- retrieval first,
- verified write-back second,
- compact outputs throughout.
