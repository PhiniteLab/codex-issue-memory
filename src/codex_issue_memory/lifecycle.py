from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import atexit
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile
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
        self._slot: int | None = None
        self._acquired = False
        self._started = False
        self._initialized = False
        self._start_time: str | None = None
        if register_atexit:
            atexit.register(self.release)

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

    def _collect_active_slots(self) -> list[dict[str, Any]]:
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
            "parent_pid": os.getppid(),
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
        aggregate = {
            "running": bool(active_slots),
            "active_count": len(active_slots),
            "active_slots": active_slots,
            "pid": int(primary["pid"]) if primary else None,
            "parent_pid": int(primary["parent_pid"]) if primary and primary.get("parent_pid") is not None else None,
            "started_at": primary["started_at"] if primary else None,
            "initialized_at": primary["initialized_at"] if primary else None,
            "lock_acquired": bool(active_slots),
            "enforce_single_instance": self.max_instances == 1 if self.max_instances is not None else False,
            "max_instances": self.max_instances,
            "launch_count": launch_count,
            "status_path": str(self.status_path),
            "lock_path": str(self.slots_dir),
            "db_path": str(self.settings.db_path),
            "state_dir": str(self.settings.state_dir),
            "command": str(primary["command"]) if primary else "",
            "owner_key": str(primary.get("owner_key") or "") if primary else None,
            "owner_key_env": str(primary.get("owner_key_env") or "") if primary else None,
            "owner_role": str(primary.get("owner_role") or "") if primary else None,
            "note": note,
            "updated_at": _utc_now(),
        }
        _atomic_write_json(self.status_path, aggregate)

    def start(self) -> None:
        if self._started:
            return
        if self.settings.server_require_owner_key and not self.settings.server_owner_key:
            raise RuntimeError(
                "issue-memory MCP owner key is required but missing."
                f" owner_key_env={self.settings.server_owner_key_env!r}"
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
                    raise RuntimeError("issue-memory MCP could not acquire an unbounded lifecycle slot.")
        else:
            for slot in range(self.max_instances):
                if self._try_acquire_slot(slot):
                    break
        if not self._acquired:
            self._release_lock_handle(self._owner_lock_handle)
            self._owner_lock_handle = None
            active = self._collect_active_slots()
            active_pids = [int(item["pid"]) for item in active if int(item.get("pid", 0) or 0) > 0]
            suffix = f" active_pids={active_pids}" if active_pids else ""
            raise RuntimeError(
                f"issue-memory MCP server instance cap reached. max_instances={self.max_instances}.{suffix}"
            )
        self._start_time = _utc_now()
        self._started = True
        self._write_slot_status(running=True, note="server-started")
        self._write_aggregate_status(note="server-started")

    def mark_initialized(self) -> None:
        if self._initialized:
            return
        self._initialized = True
        if self._started:
            self._write_slot_status(running=True, note="app-initialized")
            self._write_aggregate_status(note="app-initialized")

    def release(self) -> None:
        if not self._started:
            return
        try:
            self._write_slot_status(running=False, note="server-stopped")
        except OSError:
            pass
        self._release_lock_handle(self._lock_handle)
        self._release_lock_handle(self._owner_lock_handle)
        self._lock_handle = None
        self._owner_lock_handle = None
        self._acquired = False
        self._write_aggregate_status(note="server-stopped")
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
    pid = int(primary["pid"]) if primary else int(payload.get("pid", 0) or 0) or None
    process_alive = _pid_alive(pid or 0)
    launch_count = 0
    for slot in lifecycle._iter_known_slots():
        slot_payload = lifecycle._read_slot_payload(slot)
        launch_count += int(slot_payload.get("launch_count", 0) or 0)
    status = ServerLifecycleStatus(
        running=bool(active_slots),
        pid=pid,
        parent_pid=int((primary or {}).get("parent_pid", 0) or payload.get("parent_pid", 0) or 0) or None,
        started_at=(primary or {}).get("started_at") if primary else str(payload.get("started_at") or "") or None,
        initialized_at=(primary or {}).get("initialized_at") if primary else str(payload.get("initialized_at") or "") or None,
        lock_acquired=bool(active_slots),
        enforce_single_instance=lifecycle.max_instances == 1 if lifecycle.max_instances is not None else False,
        launch_count=launch_count,
        status_path=str(lifecycle.status_path),
        lock_path=str(lifecycle.slots_dir),
        db_path=str(settings.db_path),
        state_dir=str(settings.state_dir),
        command=str((primary or {}).get("command") or payload.get("command", "") or ""),
        process_alive=process_alive,
        max_instances=lifecycle.max_instances,
        active_count=active_count,
        active_slots=active_slots,
        assigned_slot=int(primary["slot"]) if primary else None,
        owner_key=str((primary or {}).get("owner_key") or payload.get("owner_key", "") or "") or None,
        owner_key_env=str((primary or {}).get("owner_key_env") or payload.get("owner_key_env", "") or "") or None,
        owner_role=str((primary or {}).get("owner_role") or payload.get("owner_role", "") or "") or None,
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
