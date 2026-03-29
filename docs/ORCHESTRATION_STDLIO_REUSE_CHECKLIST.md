# Orchestration-Side Real stdio Reuse Checklist

Use this checklist when you want to prove that a live Codex runtime is not merely rejecting duplicate same-owner launches, but is actually reusing the already-open `issue_memory` stdio MCP connection for parent and subagent work.

## What the repository can prove by itself

Before you run any live orchestration check, verify the repo-side contract first:

```bash
issue-memory-maint e2e-mcp-reuse-harness --json
```

That harness proves these repository-side facts:

- the main conversation owner can start one MCP server
- a subagent thread resolves back to the same main-conversation owner key
- a duplicate launch for that same owner is rejected with exit code `75`
- a different main-conversation owner can coexist at the same time

This is necessary, but not sufficient, for true shared stdio reuse.

## What this checklist is trying to prove

The live orchestration check is trying to answer a stronger question:

> After the duplicate same-owner launch is rejected, does the Codex runtime route the subagent through the already-open parent MCP connection instead of failing or launching a second one?

Only the launcher / orchestration layer can prove that.

## Preconditions

Before testing in a live Codex session:

1. `~/.codex/config.toml` contains exactly one `[mcp_servers.issue_memory]` block.
2. The live MCP env requires an owner key and uses the preferred env name:
   - `ISSUE_MEMORY_SERVER_REQUIRE_OWNER_KEY = "1"`
   - `ISSUE_MEMORY_SERVER_OWNER_KEY_ENV = "ISSUE_MEMORY_MAIN_CONVERSATION_KEY"`
   - `ISSUE_MEMORY_MAX_MCP_INSTANCES = "0"`
   - `ISSUE_MEMORY_SERVER_DUPLICATE_EXIT_CODE = "75"`
3. `issue-memory-maint doctor --mode shadow --max-instances 0` passes.
4. `issue-memory-maint e2e-mcp-reuse-harness --json` passes.
5. Codex has been restarted after any config change.

## Live main-conversation checklist

In one fresh main conversation:

1. Trigger a real `issue_memory` tool call from the main conversation.
2. Confirm the call succeeds.
3. In a parallel shell, inspect:

   ```bash
   issue-memory-maint server-status
   ```

4. Confirm:
   - `active_count` is `1`
   - `owner_key` is populated
   - `owner_role` is `main` or otherwise clearly the main conversation

## Live subagent checklist

While the same main conversation is still active:

1. Spawn a subagent that also needs `issue_memory`.
2. Trigger a real `issue_memory` tool call from that subagent.
3. Confirm the subagent request succeeds.
4. Re-run:

   ```bash
   issue-memory-maint server-status
   ```

5. Confirm:
   - there is still only one active owner slot for that main conversation
   - no second same-owner process remains alive
   - the subagent tool call did not fail just because a duplicate launch returned `75`

## Strong evidence of true orchestration-side reuse

You have strong evidence of real stdio reuse when all of the following are true:

- main-conversation tool calls succeed
- subagent tool calls also succeed in the same conversation
- `server-status` still shows one active slot for that owner
- duplicate same-owner launch behavior is controlled rather than noisy
- the subagent does not require a second independent `issue_memory` process to answer

## Second-conversation coexistence checklist

Open a second independent main conversation and repeat a real `issue_memory` call there.

Then run:

```bash
issue-memory-maint server-status
```

Expected result:

- two distinct owner keys are active
- each main conversation has its own owner slot
- subagents inside either conversation still resolve back to their own parent owner

## What counts as failure

Treat any of these as a failed orchestration-side reuse check:

- the subagent tool call fails after a duplicate same-owner launch
- the runtime keeps retrying launches with the same owner key
- multiple same-owner processes remain active
- the subagent resolves to a different owner key than the parent main conversation
- the runtime surfaces duplicate-launch noise to the user instead of reusing the parent MCP

## Important limitation

Passing this checklist still does **not** mean the repository itself can attach a new stdio client to an already-running process.

The repository can only:

- resolve owner lineage
- reject duplicate same-owner launches with `75`
- expose status that helps you diagnose reuse

The actual stdio-connection reuse behavior belongs to the Codex launcher / orchestration layer.
