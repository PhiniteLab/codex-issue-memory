# Project issue-memory policy

- Use `project_scope` equal to this repository name for repo-specific failures.
- Escalate to `global` only when a fix is broadly reusable across multiple repos.
- Before deeper debugging, call `issue_match` with:
  - the shortest meaningful failing excerpt,
  - the exact failing command,
  - the most relevant file path.
- After a fix is verified, save only normalized reusable knowledge via `issue_record_resolution`.
- Do not store one-off typos, large logs, or speculative fixes.
