# codex-issue-memory — 02_lifecycle_breakpoints

## Breakpoint 1 — owner identity collapses into per-process synthetic identity

### Code path
- direct env / alias env: `src/codex_issue_memory/settings.py:103-147`
- parent-process lineage: `src/codex_issue_memory/settings.py:223-283`
- recent-session inference: `src/codex_issue_memory/settings.py:286-321`
- synthetic fallback: `src/codex_issue_memory/settings.py:324-327`

### Live evidence
- parent host PID `11129` lacks `ISSUE_MEMORY_MAIN_CONVERSATION_KEY` and `CODEX_THREAD_ID`
- child PID `14034` also lacks them
- aggregate status file records `ISSUE_MEMORY_SYNTHETIC_OWNER_KEY` as the owner source

### Why it breaks lifecycle
Same-conversation dedup is impossible if each process receives a different owner identity.

## Breakpoint 2 — global slot cap is disabled

### Code path
- host config: `~/.codex/config.toml:213-239`
- setting parse: `src/codex_issue_memory/settings.py` max-instance parse block
- unbounded slot loop: `src/codex_issue_memory/lifecycle.py:357-366`

### Why it breaks lifecycle
Every successful launch can become another resident process.

## Breakpoint 3 — repo liveness depends on transport closure, not parent liveness

### Code path
- repo main loop: `src/codex_issue_memory/server.py:277-290`
- FastMCP stdio handoff: `mcp/server/fastmcp/server.py:753-760`
- async stdin loop: `mcp/server/stdio.py:60-71`
- session receive loop: `mcp/shared/session.py:351-357`

### Why it breaks lifecycle
`release()` only runs after `mcp.run()` returns. If stdin never closes, repo cleanup never runs.

## Breakpoint 4 — parent PID is observed but never watched

### Code path
- synthetic owner uses `os.getppid()`: `src/codex_issue_memory/settings.py:324-327`
- parent env scan starts at `os.getppid()`: `src/codex_issue_memory/settings.py:330-386`
- lifecycle status records `parent_pid`: `src/codex_issue_memory/lifecycle.py:282-296`, `src/codex_issue_memory/lifecycle.py:312-330`, `src/codex_issue_memory/lifecycle.py:432-449`

### Why it breaks lifecycle
The repo knows the parent identity, but there is no rule of the form “parent gone => shutdown now”.

## Breakpoint 5 — `initialized_at` is not the MCP initialize-handshake timestamp

### Code path
- app construction and `mark_initialized()`: `src/codex_issue_memory/server.py:44-48`
- lifecycle update: `src/codex_issue_memory/lifecycle.py:385-391`

### Why it matters
`initialized_at` means “tool app lazily instantiated”, not “host/server MCP handshake succeeded”. It cannot prove host-side initialize success.

## Breakpoint 6 — slot status may preserve an older `initialized_at`

### Code path
- slot-status write logic: `src/codex_issue_memory/lifecycle.py:270-302`

The relevant field write is:

```python
"initialized_at": _utc_now() if self._initialized else previous.get("initialized_at")
```

### Why it matters
Lifecycle status is useful for occupancy forensics, but imperfect for initialize-handshake forensics.

## Breakpoint 7 — no repo-local idle timeout
No repo code enforces shutdown of an unused but still pipe-connected stdio child.

## Net effect
1. host creates a fresh session/thread
2. host spawns a fresh stdio child
3. child receives no stable conversation owner identity
4. child synthesizes a new owner key
5. owner lock does not collide
6. slot allocation admits a new resident process
7. host keeps stdin pipe open
8. child never exits
