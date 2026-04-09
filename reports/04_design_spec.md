# codex-issue-memory — 04_design_spec

## Goal
Design a backward-compatible, minimally invasive fix set that enforces **parent-session scoped singleton MCP** behavior without breaking current stdio deployments.

## Design constraints
1. No breaking change to existing stdio MCP protocol behavior.
2. Preserve current explicit owner-key contract as the primary path.
3. Add degraded-mode safeguards for hosts that fail to inject conversation identity.
4. Make stale resident-process cleanup deterministic.

## A) Parent-session scoped singleton

### Primary identity stays the same
Keep the existing preferred identity contract:
- `ISSUE_MEMORY_MAIN_CONVERSATION_KEY`
- `CODEX_THREAD_ID` lineage resolving to root main conversation

### Add a secondary host-session key
Introduce an optional host session marker, for example:
- `CODEX_SESSION_ID`
- or `CODEX_APP_SERVER_SESSION`

Persist it into slot status for diagnostics and use it as a secondary dedup dimension.

### Behavioral rule
- if stable owner key exists: current owner-lock behavior remains primary
- if owner key is missing but host session key exists: reject additional same-session launches before synthetic fallback
- synthetic per-process owner becomes a **last-resort degraded mode**, not the normal anonymous path

## B) Duplicate rejection strategy

### Current failure mode
Current synthetic identity is per-process: `synthetic-process-<ppid>-<pid>`.
That guarantees non-collision and defeats reuse.

### Minimal change
When synthetic fallback is unavoidable, synthesize the **session**, not the process:
- `synthetic-session-<codex-session-id>`
- weaker fallback: `synthetic-parent-<ppid>`
- final fallback only when nothing else exists: current `synthetic-process-<ppid>-<pid>`

### Compatibility
Gate session-scoped synthetic mode behind an env flag for rollout safety.

## C) Idle timeout strategy
Add optional envs:
- `ISSUE_MEMORY_SERVER_IDLE_TIMEOUT_SEC`
- `ISSUE_MEMORY_SERVER_IDLE_TIMEOUT_MODE = transport|tool-use`

Behavior:
- if no inbound transport traffic for N seconds, log and exit cleanly
- default disabled for backward compatibility

## D) Parent-death handling
Add optional watchdog:
- poll `os.getppid()` or use `pidfd` where available
- if the designated parent session host disappears, close transport and call `release()`

Minimal implementation shape:
- background task started immediately after `lifecycle.start()`
- first rollout disabled behind env flag
- write lifecycle note like `parent-death-shutdown`

## E) stdin EOF shutdown hardening
Repo already obeys stdin EOF via upstream MCP stdio runtime. Keep protocol unchanged.

Add only diagnostics:
- lifecycle notes such as `waiting-stdio`, `transport-closed`, `stdio-eof`
- optional stderr debug under env flag

## F) Stale lock / stale slot recovery
At startup, before taking a new slot:
1. read existing slot payload
2. if payload says running but `pid` is dead, treat it as stale
3. rewrite status as stale-cleared and continue

This should be status cleanup only; actual flock ownership is still determined by OS lock acquisition.

## G) Host obligations
Host/orchestrator must:
1. inject `ISSUE_MEMORY_MAIN_CONVERSATION_KEY` for every launch, or inject a stable session/thread marker that deterministically resolves to the same main conversation
2. treat duplicate exit code `75` as a reuse signal, not as an unrecoverable startup failure
3. close old stdio pipes when a session/thread is abandoned
4. avoid spawning a fresh server when reusing an already-owned conversation/session transport is intended

## H) Why this is backward-compatible
- explicit owner-key users keep current behavior
- default stdio protocol stays unchanged
- new safeguards can be disabled behind env flags
- repo change surface is limited to identity resolution, watchdog, idle timeout, and diagnostics
