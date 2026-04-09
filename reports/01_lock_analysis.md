# codex-issue-memory — 01_lock_analysis

## 1) Lock inventory

### Owner lock
- Path generator: `src/codex_issue_memory/lifecycle.py:160-163`
- Name pattern: `issue_memory_mcp_owner_<sha256(owner_key)[:24]>.lock`
- Acquisition helper: `src/codex_issue_memory/lifecycle.py:190-211`
- Meaning: **one live process per resolved owner key**

### Slot lock
- Path generator: `src/codex_issue_memory/lifecycle.py:137-141`
- Name pattern: `issue_memory_mcp_slot_<n>.lock`
- Acquisition helper: `src/codex_issue_memory/lifecycle.py:168-188`
- Meaning: **exclusive occupancy of one numeric slot**

These locks protect different dimensions:
- owner lock = logical conversation ownership
- slot lock = physical resident-process slot

## 2) Acquisition order
`start()` enforces this order at `src/codex_issue_memory/lifecycle.py:336-383`:

1. verify owner-key posture
2. acquire owner lock
3. acquire slot lock
4. write slot status + aggregate status

This means duplicate same-owner launches are blocked **only if** they resolve to the same owner key.

## 3) Release points
Locks are released only in `release()`:
- slot unlock: `src/codex_issue_memory/lifecycle.py:400`
- owner unlock: `src/codex_issue_memory/lifecycle.py:401`
- aggregate rewrite and state clear: `src/codex_issue_memory/lifecycle.py:405-408`

There is no background stale-lock reaper, no TTL, and no watchdog-triggered cleanup path in the runtime.

## 4) What owner lock represents
Owner lock represents the resolved conversation identity coming from `Settings.from_env()`.

Owner resolution chain:
- direct env / alias env: `src/codex_issue_memory/settings.py:103-147`
- `CODEX_THREAD_ID` lineage: same chain plus `src/codex_issue_memory/settings.py:223-283`
- recent-session inference: `src/codex_issue_memory/settings.py:286-321`
- synthetic fallback: `src/codex_issue_memory/settings.py:324-327`

The decisive synthetic code path is:

```python
# src/codex_issue_memory/settings.py:324-327
return f"synthetic-process-{os.getppid()}-{os.getpid()}"
```

## 5) What slot lock represents
Slot lock is not an ownership guarantee. It is only a placement index.

Because `~/.codex/config.toml:213-239` sets `ISSUE_MEMORY_MAX_MCP_INSTANCES = "0"`, `src/codex_issue_memory/settings.py:83-93` converts that to `max_mcp_instances = None`.

Then `start()` uses the unbounded slot search:

```python
# src/codex_issue_memory/lifecycle.py:357-366
if self.max_instances is None:
    slot = 0
    while not self._acquired:
        if self._try_acquire_slot(slot):
            break
        slot += 1
```

## 6) Why the lock system does not stop the observed duplicates

### Step A — host does not provide a stable conversation key
Live child env `14034` contained static issue-memory envs but no `ISSUE_MEMORY_MAIN_CONVERSATION_KEY` and no `CODEX_THREAD_ID`.

Parent env `/proc/11129/environ` also lacked both `ISSUE_MEMORY_MAIN_CONVERSATION_KEY` and `CODEX_THREAD_ID`.

### Step B — repo falls back to a per-process synthetic owner
With `SERVER_REQUIRE_OWNER_KEY=1`, `ISSUE_MEMORY_SERVER_OWNER_KEY_ENV=ISSUE_MEMORY_MAIN_CONVERSATION_KEY`, and synthetic fallback enabled, the resolved owner becomes unique per PID.

### Step C — unique synthetic owner means unique owner-lock file
Because owner-lock filename hashes the owner key, `synthetic-process-11129-14034` and `synthetic-process-11129-21012` generate different owner-lock paths.

### Step D — slot layer is unbounded
Once owner-lock dedup is defeated, each new launch simply claims another free slot.

## 7) Live proof
Current aggregate status file:
- `/home/mehmet/.local/state/codex-issue-memory/issue_memory_mcp_status.json`

Current observed facts from that file:
- `active_count = 15`
- `parent_pid = 11129`
- `owner_key_env = ISSUE_MEMORY_SYNTHETIC_OWNER_KEY`
- active slots use synthetic keys such as `synthetic-process-11129-14034` and `synthetic-process-11129-21012`

Therefore the lock system is not malfunctioning. It is behaving exactly as designed against **different owner identities**.

## 8) Root conclusion
The lock system fails to prevent duplicates here because:
1. the host launches multiple children
2. each child resolves to a different synthetic owner
3. owner-lock collision never occurs
4. slot allocation is globally unbounded
