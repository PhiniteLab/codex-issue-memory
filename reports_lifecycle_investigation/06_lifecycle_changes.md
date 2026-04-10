# 06_lifecycle_changes

## Effective behavior
1. **Duplicate reject is parent-scoped when enabled**
   - parent lock path: `src/codex_issue_memory/lifecycle.py:187-190`
   - acquisition: `src/codex_issue_memory/lifecycle.py:240-257`
   - start-path enforcement: `src/codex_issue_memory/lifecycle.py:567-576`
   - duplicate exit code remains configured reuse signal (`75`): `src/codex_issue_memory/server.py:279-283`

2. **Lifecycle monitor now owns shutdown triggers**
   - monitor loop: `src/codex_issue_memory/lifecycle.py:386-421`
   - shutdown request funnel: `src/codex_issue_memory/lifecycle.py:351-384`
   - order is:
     - reap stale slots
     - parent death check
     - stdin EOF check
     - idle timeout check

3. **Idle timeout tracks last observed activity**
   - activity timestamp: `src/codex_issue_memory/lifecycle.py:148-150`, `286-292`
   - idle threshold from settings: `src/codex_issue_memory/settings.py:116-121`
   - aggregate exposure: `src/codex_issue_memory/lifecycle.py:541-550`, `699-702`

4. **Parent death now triggers clean shutdown path**
   - parent PID captured at start: `src/codex_issue_memory/lifecycle.py:147`
   - parent liveness check: `src/codex_issue_memory/lifecycle.py:315-317`
   - shutdown reason: `parent-death`

5. **stdin EOF now triggers clean shutdown path**
   - passive stdio polling without consuming protocol bytes: `src/codex_issue_memory/lifecycle.py:293-313`
   - shutdown reason: `stdin-eof`
   - server main suppresses lifecycle-owned `KeyboardInterrupt`: `src/codex_issue_memory/server.py:287-293`

6. **Stale slot recovery only reclaims dead PID slots**
   - stale predicate: `src/codex_issue_memory/lifecycle.py:319-323`
   - cleanup writeback: `src/codex_issue_memory/lifecycle.py:324-349`
   - no DB/backups/session cleanup was added

7. **Aggregate status now persists shutdown reason after stop**
   - latest slot fallback: `src/codex_issue_memory/lifecycle.py:426-439`
   - aggregate builder fallback: `src/codex_issue_memory/lifecycle.py:526-555`
   - read API fallback: `src/codex_issue_memory/lifecycle.py:658-712`
