# 08_runtime_proof

## Controlled duplicate-proof run
Command class: isolated runtime launch using the live interpreter and config-derived env, but repo-local temp state dirs.

Observed result:
```json
{
  "issue_duplicate_returncode": 75,
  "issue_duplicate_stderr": "issue-memory MCP parent process already has active instance. parent_pid=36551. active_pids=[36552]",
  "pgrep_lines": [
    "36552 /home/mehmet/infra/codex-issue-memory/.venv/bin/python -m codex_issue_memory.server"
  ]
}
```

Interpretation:
- first instance stayed alive
- second same-parent spawn was rejected with **exit 75**
- process table showed **max 1 issue-memory instance** during the controlled proof window

## Live host cleanup + check
After removing the stale pre-patch issue-memory process and leaving the config-enabled instance running, the exact runtime check was:

```bash
pgrep -af "codex_issue_memory.server|rl_developer_memory.server" | grep -v '/bin/bash -c'
```

Observed stable output after a second check:
```text
36899 /home/mehmet/infra/codex-issue-memory/.venv/bin/python -m codex_issue_memory.server
36900 /home/mehmet/infra/rl-developer-memory/.venv/bin/python -m rl_developer_memory.server
```

Issue-memory conclusion:
- no duplicate `codex_issue_memory.server` process remained
- surviving live process carried the new parent-singleton env from `~/.codex/config.toml:227-229`

## Residual note
- The stale live duplicate was a **pre-patch / pre-config** survivor. Runtime proof required removing it once; after that, the live set stabilized at one process.
