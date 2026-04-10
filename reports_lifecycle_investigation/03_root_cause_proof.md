# codex-issue-memory — 03_root_cause_proof

## Claim 1 — duplicate spawn is host-driven, not created inside `codex_issue_memory`

### Proof
1. Repo code has exactly one startup path: `src/codex_issue_memory/server.py:277-290` plus `src/codex_issue_memory/server.py:293-294`. There is no fork/respawn logic in the repo.
2. Live process tree shows many `codex_issue_memory` children under the **same** parent PID `11129` (Codex app-server).
   - sample child PIDs: 14034, 14654, 14896, 14931, 16688, 16829, 17045, 17065, 17081, 17113, 17877, 17884, 21012, 21019
   - same parent command: `/home/mehmet/.vscode-server/extensions/openai.chatgpt-26.5401.11717-linux-x64/bin/linux-x86_64/codex app-server --analytics-default-enabled`
3. Session metadata timestamps correlate with new child creation:
   - main session `rollout-2026-04-08T16-34-56-019d6d4d-9cfb-7b52-a3cb-5197b142524e.jsonl`
   - subagent sessions `rollout-2026-04-08T16-35-54-019d6d4e-8054-7861-ba41-e26c4aa4288c.jsonl` and `rollout-2026-04-08T16-35-54-019d6d4e-80b3-7c61-9537-0c13c2668351.jsonl`
   - RL auditor session `rollout-2026-04-08T16-37-40-019d6d50-2039-7573-8cfe-2d9f48c51c1f.jsonl`
   - later refactorer session `rollout-2026-04-08T16-47-03-019d6d58-b425-7592-b04e-c8d818411752.jsonl`
4. Codex log proves MCP manager initialization occurs during session init:
   - `~/.codex/log/codex-tui.log:36-37` — `session_init.mcp_manager_init` with `enabled_mcp_server_count=2`

### Conclusion
The duplicate processes are being created by the host/session orchestration layer.

---

## Claim 2 — lock system cannot reject these duplicates because every child becomes a different owner

### Proof
1. Host config requires owner-key mode and still allows synthetic fallback:
   - `~/.codex/config.toml:213-239`
   - `SERVER_REQUIRE_OWNER_KEY = "1"`
   - `ISSUE_MEMORY_SERVER_OWNER_KEY_ENV = "ISSUE_MEMORY_MAIN_CONVERSATION_KEY"`
   - `SERVER_ALLOW_SYNTHETIC_OWNER_KEY = "1"`
   - `ISSUE_MEMORY_MAX_MCP_INSTANCES = "0"`
2. Live child env `14034` contains only static server config vars; it does **not** contain `ISSUE_MEMORY_MAIN_CONVERSATION_KEY` and does **not** contain `CODEX_THREAD_ID`.
3. Parent env `/proc/11129/environ` also lacks both fields.
4. Repo owner resolution chain falls through to synthetic fallback: `src/codex_issue_memory/settings.py:103-147`, `223-283`, `286-321`, `324-327`.
5. Live aggregate status file `/home/mehmet/.local/state/codex-issue-memory/issue_memory_mcp_status.json` shows:
   - `owner_key_env = ISSUE_MEMORY_SYNTHETIC_OWNER_KEY`
   - sample active keys `synthetic-process-11129-14034` and `synthetic-process-11129-21012`
6. Owner lock path is hashed from owner key (`src/codex_issue_memory/lifecycle.py:160-163`), so different synthetic owner keys create different owner-lock files.

### Conclusion
Lock rejection is bypassed because each child presents itself as a different owner.

---

## Claim 3 — lifecycle breaks at the host/server boundary after spawn

### Proof
1. Repo `start()` succeeds and records the child as running.
2. Upstream stdio loop is transport-bound: `mcp/server/stdio.py:60-71`, `mcp/shared/session.py:351-357`.
3. Repo cleanup occurs only after `mcp.run()` returns: `src/codex_issue_memory/server.py:277-290`.
4. The host keeps old child pipes open instead of delivering EOF.

### Pipe proof
For issue-memory child `14034`:
- child fds: `0 -> pipe:[172102]`, `1 -> pipe:[172103]`, `2 -> pipe:[172104]`
- parent still holds matching ends: `/proc/11129/fd/53 -> pipe:[172102]`, `/proc/11129/fd/55 -> pipe:[172103]`, `/proc/11129/fd/60 -> pipe:[172104]`

The same host behavior was observed for later children as well, including a newer pair with pipes `296738/296739/296740`.

### Conclusion
The lifecycle failure point is not inside repo initialization; it is the host/server transport boundary where old stdio transports remain open.

---

## Claim 4 — old processes remain alive because EOF never arrives and there is no server watchdog

### Proof
1. Local reproduction proved repo exits cleanly when stdin is closed.
2. Live old children remain attached to parent-owned pipes.
3. Parent PID is only used for metadata/fallback logic, not for liveness enforcement.
4. Repo has no idle timeout.

### Conclusion
Old processes stay alive because the host-created transport never closes, and the server has no independent shutdown trigger.

---

## Claim 5 — this incident is not best explained by handshake-retry failure

### What is proven
- repeated launches align with new Codex session/thread creation events
- earlier children remain alive concurrently instead of being replaced
- current status keeps accumulating active slots

### What is also visible in the closed-source host binary
String extraction from the Codex binary exposed host-side initialize/timeout/process-group cleanup logic:
- `required MCP servers failed to initialize:`
- `timed out waiting for initialize response from`
- `rejected initialize:`
- `closed during initialize`
- `startup_timeout_sec:`
- `tool_timeout_sec:`
- `Failed to terminate MCP process group`
- `Failed to kill MCP process group`

### Strict conclusion
The host **does** contain initialize-time failure handling, but the concrete incident here is already fully explained without invoking it: one new host spawn per new session/thread, missing conversation owner propagation, unbounded slot admission, and non-closed stdio pipes.

---

## Final proven root cause

### Host-side cause
- Codex/VS Code session initialization spawns both MCP servers again for each new thread/subagent session.
- The child env used in this incident does not contain `ISSUE_MEMORY_MAIN_CONVERSATION_KEY`.
- The child env used in this incident also does not contain `CODEX_THREAD_ID`.
- The host keeps previous stdio pipes open, so old children never get EOF.

### Server-side contribution
- dedup is owner-key-scoped, not parent-session-scoped
- synthetic fallback is enabled
- global slot cap is disabled
- there is no parent-death watchdog
- there is no idle-timeout shutdown

### Responsibility split
- **duplicate creation:** host
- **duplicate admission after launch:** server configuration + synthetic fallback + unbounded slots
- **old-process persistence:** host-kept pipes + no server watchdog/idle timeout
