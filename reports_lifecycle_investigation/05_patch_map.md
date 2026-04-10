# 05_patch_map

## Scope
- Repo: `/home/mehmet/infra/codex-issue-memory`
- Goal: parent-scoped singleton + duplicate rejection + idle/parent/EOF shutdown + stale slot recovery
- Compatibility posture:
  - code default remains env-gated (`ISSUE_MEMORY_SERVER_ENFORCE_PARENT_SINGLETON` default off in code)
  - live enablement moved to `~/.codex/config.toml:227-229`
  - reuse harness keeps parent singleton disabled in test mode (`src/codex_issue_memory/e2e_mcp_reuse_harness.py:104-106`, `143-145`)

## Minimal patch points
1. `src/codex_issue_memory/settings.py:59,112-121,218-220`
   - added lifecycle env parsing:
     - `ISSUE_MEMORY_SERVER_ENFORCE_PARENT_SINGLETON`
     - `ISSUE_MEMORY_SERVER_PARENT_INSTANCE_IDLE_TIMEOUT_SECONDS`
     - `ISSUE_MEMORY_SERVER_PARENT_INSTANCE_MONITOR_INTERVAL_SECONDS`
2. `src/codex_issue_memory/lifecycle.py:187-244,351-421,557-652`
   - duplicate guard, lifecycle monitor, shutdown controller, stale recovery, richer aggregate status
3. `src/codex_issue_memory/server.py:277-293`
   - suppress lifecycle-triggered `KeyboardInterrupt` while preserving real interrupts
4. `src/codex_issue_memory/e2e_mcp_reuse_harness.py:104-106,143-145`
   - keep legacy reuse contract by explicitly disabling parent singleton in harness env
5. `tests/test_phase6_server_lifecycle.py:256-501`
   - added lifecycle regression tests:
     - duplicate parent guard
     - stale slot cleanup
     - idle timeout
     - parent death
     - stdin EOF
6. `~/.codex/config.toml:227-229`
   - live host enablement for issue-memory singleton/idle watchdog

## Change map by concern
- Duplicate guard: `lifecycle.py:187-244`, `557-580`
- Idle timeout: `lifecycle.py:351-421`
- Parent death: `lifecycle.py:351-421`
- Stdin EOF: `lifecycle.py:293-313`, `351-421`
- Stale slot cleanup: `lifecycle.py:324-349`
- Shutdown reason persistence: `lifecycle.py:426-555`, `658-712`
- Server main-loop compatibility: `server.py:287-293`
