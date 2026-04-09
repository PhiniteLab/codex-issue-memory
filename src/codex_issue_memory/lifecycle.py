from __future__ import annotations

import atexit
import hashlib
import json
import os
import re
import select
import signal
import sys
import tempfile
import _thread
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .settings import Settings

try:
    import fcntl  # type: ignore[attr-defined]
except ImportError:  # pragma: no cover - windows fallback
    fcntl = None  # type: ignore[assignment]

try:  # pragma: no cover - windows fallback
    import msvcrt  # type: ignore[attr-defined]
except ImportError:  # pragma: no cover - posix normal path
    msvcrt = None  # type: ignore[assignment]


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", delete=False, dir=str(path.parent), encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False, sort_keys=True)
        handle.flush()
        os.fsync(handle.fileno())
        temp_name = handle.name
    os.replace(temp_name, path)


@dataclass(slots=True)
class ServerLifecycleStatus:
    running: bool
    pid: int | None
    parent_pid: int | None
    started_at: str | None
    initialized_at: str | None
    lock_acquired: bool
    enforce_single_instance: bool
    launch_count: int
    status_path: str
    lock_path: str
    db_path: str
    state_dir: str
    command: str
    process_alive: bool
    max_instances: int | None
    active_count: int
    active_slots: list[dict[str, Any]]
    assigned_slot: int | None
    owner_key: str | None
    owner_key_env: str | None
    owner_role: str | None
    shutdown_reason: str | None
    last_activity_at: str | None
    parent_singleton_enforced: bool
    idle_timeout_seconds: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "running": self.running,
            "pid": self.pid,
            "parent_pid": self.parent_pid,
            "started_at": self.started_at,
            "initialized_at": self.initialized_at,
            "lock_acquired": self.lock_acquired,
            "enforce_single_instance": self.enforce_single_instance,
            "launch_count": self.launch_count,
            "status_path": self.status_path,
            "lock_path": self.lock_path,
            "db_path": self.db_path,
            "state_dir": self.state_dir,
            "command": self.command,
            "process_alive": self.process_alive,
            "max_instances": self.max_instances,
            "active_count": self.active_count,
            "active_slots": self.active_slots,
            "assigned_slot": self.assigned_slot,
            "owner_key": self.owner_key,
            "owner_key_env": self.owner_key_env,
            "owner_role": self.owner_role,
            "shutdown_reason": self.shutdown_reason,
            "last_activity_at": self.last_activity_at,
            "parent_singleton_enforced": self.parent_singleton_enforced,
            "idle_timeout_seconds": self.idle_timeout_seconds,
        }


class MCPServerOwnerConflict(RuntimeError):
    def __init__(self, message: str, *, exit_code: int, owner_key: str = "") -> None:
        super().__init__(message)
        self.exit_code = int(exit_code)
        self.owner_key = owner_key


class MCPServerLifecycle:
    """Lifecycle tracker for stdio MCP processes with owner-key dedup and optional total cap.

    Codex launches enabled stdio MCP servers whenever a session starts. This class keeps
    the issue-memory server lightweight until the first tool call. When an owner key is
    present, only one live process may own that conversation at a time. A global total cap
    remains available only as an optional compatibility fallback.
    """

    def __init__(self, settings: Settings, *, register_atexit: bool = True) -> None:
        self.settings = settings
        self.max_instances = None if settings.max_mcp_instances is None else max(int(settings.max_mcp_instances), 1)
        self.slots_dir = settings.state_dir / "mcp_slots"
        self.status_path = settings.state_dir / "issue_memory_mcp_status.json"
        self._lock_handle: Any | None = None
        self._owner_lock_handle: Any | None = None
        self._parent_lock_handle: Any | None = None
        self._slot: int | None = None
        self._acquired = False
        self._started = False
        self._initialized = False
        self._start_time: str | None = None
        self._parent_pid = os.getppid()
        self._last_activity = time.monotonic()
        self._last_activity_at: str | None = None
        self._shutdown_reason: str | None = None
        self._monitor_stop = threading.Event()
        self._monitor_thread: threading.Thread | None = None
        if register_atexit:
            atexit.register(self.release)

    @property
    def shutdown_reason(self) -> str | None:
        return self._shutdown_reason

    def _slot_lock_path(self, slot: int) -> Path:
        return self.slots_dir / f"issue_memory_mcp_slot_{slot}.lock"

    def _slot_status_path(self, slot: int) -> Path:
        return self.slots_dir / f"issue_memory_mcp_slot_{slot}.json"

    def _known_slot_numbers(self) -> list[int]:
        slots: set[int] = set()
        for path in self.slots_dir.glob("issue_memory_mcp_slot_*.json"):
            match = re.search(r"issue_memory_mcp_slot_(\d+)\.json$", path.name)
            if match:
                slots.add(int(match.group(1)))
        for path in self.slots_dir.glob("issue_memory_mcp_slot_*.lock"):
            match = re.search(r"issue_memory_mcp_slot_(\d+)\.lock$", path.name)
            if match:
                slots.add(int(match.group(1)))
        return sorted(slots)

    def _iter_known_slots(self) -> list[int]:
        if self.max_instances is not None:
            return list(range(self.max_instances))
        return self._known_slot_numbers()

    def _owner_lock_path(self, owner_key: str) -> Path:
        digest = hashlib.sha256(owner_key.encode("utf-8")).hexdigest()[:24]
        return self.settings.server_lock_dir / f"issue_memory_mcp_owner_{digest}.lock"

    def _parent_lock_path(self) -> Path:
        digest = hashlib.sha256(f"issue-memory:parent:{self._parent_pid}".encode("utf-8")).hexdigest()[:24]
        return self.settings.server_lock_dir / f"issue_memory_mcp_parent_{digest}.lock"

    def _open_lock_handle(self, path: Path) -> Any:
        path.parent.mkdir(parents=True, exist_ok=True)
        return open(path, "a+", encoding="utf-8")

    def _try_acquire_slot(self, slot: int) -> bool:
        handle = self._open_lock_handle(self._slot_lock_path(slot))
        try:
            if fcntl is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            elif msvcrt is not None:  # pragma: no cover - windows fallback
                try:
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)  # type: ignore[attr-defined]
                except OSError:
                    handle.close()
                    return False
            else:  # pragma: no cover - unknown platform
                handle.close()
                return True
        except OSError:
            handle.close()
            return False
        self._lock_handle = handle
        self._acquired = True
        self._slot = slot
        return True

    def _try_acquire_owner_key(self) -> bool:
        owner_key = self.settings.server_owner_key
        if not owner_key:
            return True
        handle = self._open_lock_handle(self._owner_lock_path(owner_key))
        try:
            if fcntl is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            elif msvcrt is not None:  # pragma: no cover - windows fallback
                try:
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)  # type: ignore[attr-defined]
                except OSError:
                    handle.close()
                    return False
            else:  # pragma: no cover - unknown platform
                handle.close()
                return True
        except OSError:
            handle.close()
            return False
        self._owner_lock_handle = handle
        return True

    def _try_acquire_parent_lock(self) -> bool:
        if not self.settings.server_enforce_parent_singleton:
            return True
        handle = self._open_lock_handle(self._parent_lock_path())
        try:
            if fcntl is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            elif msvcrt is not None:  # pragma: no cover - windows fallback
                try:
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)  # type: ignore[attr-defined]
                except OSError:
                    handle.close()
                    return False
            else:  # pragma: no cover - unknown platform
                handle.close()
                return True
        except OSError:
            handle.close()
            return False
        self._parent_lock_handle = handle
        return True

    def _release_lock_handle(self, handle: Any | None) -> None:
        if handle is None:
            return
        try:
            if fcntl is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            elif msvcrt is not None:  # pragma: no cover - windows fallback
                try:
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)  # type: ignore[attr-defined]
                except OSError:
                    pass
        finally:
            try:
                handle.close()
            except OSError:
                pass

    def _note_activity(self) -> None:
        self._last_activity = time.monotonic()
        self._last_activity_at = _utc_now()

    def _monitor_interval(self) -> float:
        return max(float(self.settings.server_parent_instance_monitor_interval_seconds), 0.2)

    def _stdin_fd(self) -> int | None:
        try:
            fd = sys.stdin.fileno()
        except Exception:
            return None
        return fd if fd >= 0 else None

    def _observe_stdio(self) -> str | None:
        fd = self._stdin_fd()
        if fd is None or not hasattr(select, "poll"):
            return None
        try:
            poller = select.poll()
            readable_mask = getattr(select, "POLLIN", 0)
            eof_mask = getattr(select, "POLLHUP", 0) | getattr(select, "POLLERR", 0) | getattr(select, "POLLNVAL", 0)
            poller.register(fd, readable_mask | eof_mask)
            events = poller.poll(0)
        except Exception:
            return None
        if not events:
            return None
        event_mask = 0
        for _fd, mask in events:
            event_mask |= mask
        if event_mask & eof_mask:
            self._note_activity()
            return "stdin-eof"
        if event_mask & readable_mask:
            self._note_activity()
        return None

    def _check_parent_alive(self) -> bool:
        current_parent = os.getppid()
        return current_parent == self._parent_pid and _pid_alive(self._parent_pid)

    def _is_stale_slot(self, payload: dict[str, Any]) -> bool:
        if not payload:
            return False
        pid = int(payload.get("pid", 0) or 0)
        running = bool(payload.get("running", False))
        return running and pid > 0 and not _pid_alive(pid)

    def _cleanup_stale_slot(self, slot: int, payload: dict[str, Any]) -> None:
        reason = "stale-slot-pid-gone"
        payload.update(
            {
                "running": False,
                "note": f"{reason};{payload.get('note', '')}".strip(";"),
                "shutdown_reason": reason,
                "stopped_at": _utc_now(),
                "updated_at": _utc_now(),
            }
        )
        _atomic_write_json(self._slot_status_path(slot), payload)
        try:
            self._slot_lock_path(slot).unlink(missing_ok=True)
        except OSError:
            pass

    def _reap_stale_slots(self) -> None:
        for slot in self._iter_known_slots():
            payload = self._read_slot_payload(slot)
            if self._is_stale_slot(payload):
                self._cleanup_stale_slot(slot, payload)

    def _request_shutdown(self, reason: str) -> None:
        if self._shutdown_reason:
            return
        self._shutdown_reason = reason
        self._monitor_stop.set()
        try:
            if self._started:
                self._write_slot_status(running=False, note=reason)
                self._write_aggregate_status(note=reason)
        except OSError:
            pass
        fd = self._stdin_fd()
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
        try:
            pthread_kill = getattr(signal, "pthread_kill", None)
            main_ident = threading.main_thread().ident
            if pthread_kill is not None and main_ident is not None:
                pthread_kill(main_ident, signal.SIGINT)
                return
        except Exception:
            pass
        try:
            os.kill(os.getpid(), signal.SIGINT)
            return
        except Exception:
            pass
        try:
            _thread.interrupt_main()
        except RuntimeError:
            pass

    def _start_monitor(self) -> None:
        if self._monitor_thread is not None and self._monitor_thread.is_alive():
            return
        if self._stdin_fd() is None and not self.settings.server_enforce_parent_singleton and self.settings.server_parent_instance_idle_timeout_seconds <= 0:
            return

        def _monitor() -> None:
            while not self._monitor_stop.is_set():
                time.sleep(self._monitor_interval())
                if self._shutdown_reason:
                    return
                self._reap_stale_slots()
                if self.settings.server_enforce_parent_singleton and not self._check_parent_alive():
                    self._request_shutdown("parent-death")
                    return
                eof_reason = self._observe_stdio()
                if eof_reason:
                    self._request_shutdown(eof_reason)
                    return
                if self.settings.server_parent_instance_idle_timeout_seconds > 0:
                    elapsed = time.monotonic() - self._last_activity
                    if elapsed >= self.settings.server_parent_instance_idle_timeout_seconds:
                        self._request_shutdown("idle-timeout")
                        return

        self._monitor_thread = threading.Thread(target=_monitor, name="issue-memory-mcp-lifecycle", daemon=True)
        self._monitor_thread.start()

    def _read_slot_payload(self, slot: int) -> dict[str, Any]:
        path = self._slot_status_path(slot)
        if not path.exists():
            return {}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            return {}
        pid = int(payload.get("pid", 0) or 0)
        payload["process_alive"] = _pid_alive(pid)
        return payload

    def _latest_slot_payload(self) -> dict[str, Any]:
        latest: dict[str, Any] = {}
        latest_key = ""
        for slot in self._iter_known_slots():
            payload = self._read_slot_payload(slot)
            if not payload:
                continue
            candidate_key = str(
                payload.get("updated_at")
                or payload.get("stopped_at")
                or payload.get("initialized_at")
                or payload.get("started_at")
                or ""
            )
            if candidate_key >= latest_key:
                latest = payload
                latest_key = candidate_key
        return latest

    def _collect_active_slots(self) -> list[dict[str, Any]]:
        self._reap_stale_slots()
        active: list[dict[str, Any]] = []
        for slot in self._iter_known_slots():
            payload = self._read_slot_payload(slot)
            pid = int(payload.get("pid", 0) or 0)
            alive = bool(payload.get("process_alive", False))
            running = bool(payload.get("running", False)) and alive
            if not running:
                continue
            active.append(
                {
                    "slot": slot,
                    "pid": pid,
                    "parent_pid": int(payload.get("parent_pid", 0) or 0) or None,
                    "started_at": str(payload.get("started_at") or "") or None,
                    "initialized_at": str(payload.get("initialized_at") or "") or None,
                    "command": str(payload.get("command", "") or ""),
                    "lock_path": str(self._slot_lock_path(slot)),
                    "status_path": str(self._slot_status_path(slot)),
                    "process_alive": alive,
                    "owner_key": str(payload.get("owner_key") or "") or None,
                    "owner_key_env": str(payload.get("owner_key_env") or "") or None,
                    "owner_role": str(payload.get("owner_role") or "") or None,
                    "shutdown_reason": str(payload.get("shutdown_reason") or "") or None,
                    "last_activity_at": str(payload.get("last_activity_at") or "") or None,
                }
            )
        active.sort(key=lambda item: int(item["slot"]))
        return active

    def _write_slot_status(self, *, running: bool, note: str = "") -> None:
        if self._slot is None:
            return
        previous = self._read_slot_payload(self._slot)
        launch_count = int(previous.get("launch_count", 0) or 0)
        if running and (previous.get("pid") != os.getpid() or not previous.get("running", False)):
            launch_count += 1
        elif not running and self._started:
            launch_count = max(launch_count, 1)
        payload = {
            "slot": self._slot,
            "pid": os.getpid(),
            "parent_pid": self._parent_pid,
            "running": running,
            "started_at": self._start_time,
            "initialized_at": _utc_now() if self._initialized else previous.get("initialized_at"),
            "lock_acquired": self._acquired,
            "max_instances": self.max_instances,
            "launch_count": launch_count,
            "status_path": str(self._slot_status_path(self._slot)),
            "lock_path": str(self._slot_lock_path(self._slot)),
            "db_path": str(self.settings.db_path),
            "state_dir": str(self.settings.state_dir),
            "command": " ".join(sys.argv),
            "owner_key": self.settings.server_owner_key or None,
            "owner_key_env": self.settings.server_owner_key_env or None,
            "owner_role": self.settings.server_owner_role or None,
            "parent_singleton_enforced": self.settings.server_enforce_parent_singleton,
            "shutdown_reason": self._shutdown_reason,
            "last_activity_at": self._last_activity_at or previous.get("last_activity_at"),
            "note": note,
            "updated_at": _utc_now(),
        }
        if not running:
            payload["stopped_at"] = _utc_now()
        _atomic_write_json(self._slot_status_path(self._slot), payload)

    def _write_aggregate_status(self, *, note: str = "") -> None:
        active_slots = self._collect_active_slots()
        primary = active_slots[0] if active_slots else None
        launch_count = 0
        for slot in self._iter_known_slots():
            payload = self._read_slot_payload(slot)
            launch_count += int(payload.get("launch_count", 0) or 0)
        existing: dict[str, Any] = {}
        if self.status_path.exists():
            try:
                existing = json.loads(self.status_path.read_text(encoding="utf-8"))
            except (OSError, ValueError, json.JSONDecodeError):
                existing = {}
        latest = self._latest_slot_payload()
        fallback = primary or latest or existing
        aggregate = {
            "running": bool(active_slots),
            "active_count": len(active_slots),
            "active_slots": active_slots,
            "pid": int((primary or fallback).get("pid", 0) or 0) or None,
            "parent_pid": int((primary or fallback).get("parent_pid", 0) or 0) or None,
            "started_at": (primary or fallback).get("started_at") if (primary or fallback) else None,
            "initialized_at": (primary or fallback).get("initialized_at") if (primary or fallback) else None,
            "lock_acquired": bool(active_slots),
            "enforce_single_instance": self.max_instances == 1 if self.max_instances is not None else False,
            "max_instances": self.max_instances,
            "launch_count": launch_count,
            "status_path": str(self.status_path),
            "lock_path": str(self.slots_dir),
            "db_path": str(self.settings.db_path),
            "state_dir": str(self.settings.state_dir),
            "command": str((primary or fallback).get("command", "") or ""),
            "owner_key": str((primary or fallback).get("owner_key", "") or "") or None,
            "owner_key_env": str((primary or fallback).get("owner_key_env", "") or "") or None,
            "owner_role": str((primary or fallback).get("owner_role", "") or "") or None,
            "shutdown_reason": str((primary or fallback).get("shutdown_reason", "") or "") or None,
            "parent_singleton_enforced": self.settings.server_enforce_parent_singleton,
            "idle_timeout_seconds": self.settings.server_parent_instance_idle_timeout_seconds,
            "last_activity_at": str((primary or fallback).get("last_activity_at", "") or "") or None,
            "note": note,
            "updated_at": _utc_now(),
        }
        _atomic_write_json(self.status_path, aggregate)

    def start(self) -> None:
        if self._started:
            return
        self._note_activity()
        self._reap_stale_slots()
        if self.settings.server_require_owner_key and not self.settings.server_owner_key:
            raise RuntimeError(
                "issue-memory MCP owner key is required but missing."
                f" owner_key_env={self.settings.server_owner_key_env!r}"
            )
        if self.settings.server_enforce_parent_singleton and not self._try_acquire_parent_lock():
            active = [item for item in self._collect_active_slots() if int(item.get("parent_pid") or 0) == self._parent_pid]
            active_pids = [int(item["pid"]) for item in active if int(item.get("pid", 0) or 0) > 0]
            suffix = f" active_pids={active_pids}" if active_pids else ""
            raise MCPServerOwnerConflict(
                "issue-memory MCP parent process already has active instance."
                f" parent_pid={self._parent_pid!r}.{suffix}",
                exit_code=self.settings.server_duplicate_exit_code,
                owner_key=self.settings.server_owner_key,
            )
        if not self._try_acquire_owner_key():
            active = [
                item for item in self._collect_active_slots() if str(item.get("owner_key") or "") == self.settings.server_owner_key
            ]
            active_pids = [int(item["pid"]) for item in active if int(item.get("pid", 0) or 0) > 0]
            suffix = f" active_pids={active_pids}" if active_pids else ""
            role_suffix = f" owner_role={self.settings.server_owner_role!r}." if self.settings.server_owner_role else "."
            raise MCPServerOwnerConflict(
                "issue-memory MCP owner key already active."
                f" owner_key={self.settings.server_owner_key!r}{role_suffix}{suffix}",
                exit_code=self.settings.server_duplicate_exit_code,
                owner_key=self.settings.server_owner_key,
            )
        if self.max_instances is None:
            slot = 0
            while not self._acquired:
                if self._try_acquire_slot(slot):
                    break
                slot += 1
                if slot > 10000:
                    self._release_lock_handle(self._owner_lock_handle)
                    self._owner_lock_handle = None
                    self._release_lock_handle(self._parent_lock_handle)
                    self._parent_lock_handle = None
                    raise RuntimeError("issue-memory MCP could not acquire an unbounded lifecycle slot.")
        else:
            for slot in range(self.max_instances):
                if self._try_acquire_slot(slot):
                    break
        if not self._acquired:
            self._release_lock_handle(self._owner_lock_handle)
            self._owner_lock_handle = None
            self._release_lock_handle(self._parent_lock_handle)
            self._parent_lock_handle = None
            active = self._collect_active_slots()
            active_pids = [int(item["pid"]) for item in active if int(item.get("pid", 0) or 0) > 0]
            suffix = f" active_pids={active_pids}" if active_pids else ""
            raise RuntimeError(f"issue-memory MCP server instance cap reached. max_instances={self.max_instances}.{suffix}")
        self._start_time = _utc_now()
        self._started = True
        self._shutdown_reason = None
        self._write_slot_status(running=True, note="server-started")
        self._write_aggregate_status(note="server-started")
        self._start_monitor()

    def mark_initialized(self) -> None:
        if self._initialized:
            return
        self._initialized = True
        self._note_activity()
        if self._started:
            self._write_slot_status(running=True, note="app-initialized")
            self._write_aggregate_status(note="app-initialized")

    def release(self) -> None:
        if not self._started:
            return
        self._monitor_stop.set()
        if self._monitor_thread is not None and self._monitor_thread is not threading.current_thread():
            self._monitor_thread.join(timeout=0.5)
        note = self._shutdown_reason or "server-stopped"
        try:
            self._write_slot_status(running=False, note=note)
        except OSError:
            pass
        self._release_lock_handle(self._lock_handle)
        self._release_lock_handle(self._owner_lock_handle)
        self._release_lock_handle(self._parent_lock_handle)
        self._lock_handle = None
        self._owner_lock_handle = None
        self._parent_lock_handle = None
        self._acquired = False
        try:
            self._write_aggregate_status(note=note)
        except OSError:
            pass
        self._slot = None
        self._started = False
        self._initialized = False


def read_server_lifecycle_status(settings: Settings) -> ServerLifecycleStatus:
    lifecycle = MCPServerLifecycle(settings, register_atexit=False)
    payload: dict[str, Any] = {}
    if lifecycle.status_path.exists():
        try:
            payload = json.loads(lifecycle.status_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            payload = {}
    active_slots = lifecycle._collect_active_slots()
    active_count = len(active_slots)
    primary = active_slots[0] if active_slots else None
    latest = lifecycle._latest_slot_payload()
    fallback = primary or latest or payload
    pid = int((fallback or {}).get("pid", 0) or 0) or None
    process_alive = _pid_alive(pid or 0)
    launch_count = 0
    for slot in lifecycle._iter_known_slots():
        slot_payload = lifecycle._read_slot_payload(slot)
        launch_count += int(slot_payload.get("launch_count", 0) or 0)
    status = ServerLifecycleStatus(
        running=bool(active_slots),
        pid=pid,
        parent_pid=int((fallback or {}).get("parent_pid", 0) or 0) or None,
        started_at=str((fallback or {}).get("started_at") or "") or None,
        initialized_at=str((fallback or {}).get("initialized_at") or "") or None,
        lock_acquired=bool(active_slots),
        enforce_single_instance=lifecycle.max_instances == 1 if lifecycle.max_instances is not None else False,
        launch_count=launch_count,
        status_path=str(lifecycle.status_path),
        lock_path=str(lifecycle.slots_dir),
        db_path=str(settings.db_path),
        state_dir=str(settings.state_dir),
        command=str((fallback or {}).get("command") or ""),
        process_alive=process_alive,
        max_instances=lifecycle.max_instances,
        active_count=active_count,
        active_slots=active_slots,
        assigned_slot=int(primary["slot"]) if primary else None,
        owner_key=str((fallback or {}).get("owner_key") or "") or None,
        owner_key_env=str((fallback or {}).get("owner_key_env") or "") or None,
        owner_role=str((fallback or {}).get("owner_role") or "") or None,
        shutdown_reason=str((fallback or {}).get("shutdown_reason") or "") or None,
        last_activity_at=str((fallback or {}).get("last_activity_at") or "") or None,
        parent_singleton_enforced=bool(settings.server_enforce_parent_singleton),
        idle_timeout_seconds=int(settings.server_parent_instance_idle_timeout_seconds),
    )
    try:
        lifecycle._write_aggregate_status(note="status-read")
    except OSError:
        pass
    return status


__all__ = [
    "MCPServerLifecycle",
    "MCPServerOwnerConflict",
    "ServerLifecycleStatus",
    "read_server_lifecycle_status",
]
