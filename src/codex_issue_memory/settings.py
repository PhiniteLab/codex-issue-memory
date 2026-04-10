from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import os
import socket


@dataclass(frozen=True)
class Settings:
    """Runtime settings loaded from environment variables."""

    issue_memory_home: Path
    db_path: Path
    state_dir: Path
    log_dir: Path
    backup_dir: Path
    windows_backup_target: Path | None
    calibration_profile_path: Path
    local_backup_keep: int
    mirror_backup_keep: int
    hostname: str
    default_user_scope: str
    match_accept_threshold: float
    match_weak_threshold: float
    ambiguity_margin: float
    session_ttl_seconds: int
    telemetry_enabled: bool
    enable_dense_retrieval: bool
    dense_embedding_dim: int
    dense_candidate_limit: int
    dense_similarity_floor: float
    dense_model_name: str
    enable_strategy_bandit: bool
    enable_strategy_bandit_shadow_mode: bool
    strategy_overlay_scale: float
    variant_overlay_scale: float
    safe_override_margin: float
    minimum_strategy_evidence: int
    strategy_half_life_days: int
    variant_half_life_days: int
    enable_preference_rules: bool
    preference_overlay_scale: float
    max_preference_adjustment: float
    guardrail_limit: int
    telemetry_retention_days: int
    resolved_review_retention_days: int
    enable_redaction: bool
    enable_calibration_profile: bool
    session_decay_half_life_minutes: float
    implicit_feedback_timeout_minutes: int
    enable_cross_session_learning: bool
    auto_rejection_threshold: int
    max_mcp_instances: int | None
    server_lock_dir: Path
    server_duplicate_exit_code: int
    server_require_owner_key: bool
    server_owner_key: str
    server_owner_key_env: str
    server_owner_role: str
    server_enforce_parent_singleton: bool
    server_parent_instance_idle_timeout_seconds: int
    server_parent_instance_monitor_interval_seconds: float
    fp_rate_alarm_threshold: float
    feedback_batch_window_seconds: int
    feedback_batch_fp_review_threshold: int
    env_json_max_chars: int
    verification_output_max_chars: int
    note_max_chars: int

    @classmethod
    def from_env(cls) -> "Settings":
        home = Path(
            os.environ.get(
                "ISSUE_MEMORY_HOME",
                Path.home() / ".local" / "share" / "codex-issue-memory",
            )
        ).expanduser()
        db_path = Path(
            os.environ.get("ISSUE_MEMORY_DB_PATH", home / "issue_memory.sqlite3")
        ).expanduser()
        state_dir = Path(
            os.environ.get(
                "ISSUE_MEMORY_STATE_DIR",
                Path.home() / ".local" / "state" / "codex-issue-memory",
            )
        ).expanduser()
        backup_dir = Path(
            os.environ.get("ISSUE_MEMORY_BACKUP_DIR", home / "backups")
        ).expanduser()
        log_dir = Path(
            os.environ.get("ISSUE_MEMORY_LOG_DIR", state_dir / "log")
        ).expanduser()
        calibration_profile_path = Path(
            os.environ.get(
                "ISSUE_MEMORY_CALIBRATION_PROFILE_PATH",
                state_dir / "calibration_profile.json",
            )
        ).expanduser()
        server_lock_dir = Path(
            os.environ.get("ISSUE_MEMORY_SERVER_LOCK_DIR", state_dir / "run")
        ).expanduser()

        raw_windows_target = os.environ.get(
            "ISSUE_MEMORY_WINDOWS_BACKUP_TARGET", ""
        ).strip()
        windows_target = (
            Path(raw_windows_target).expanduser() if raw_windows_target else None
        )

        require_owner_key = os.environ.get(
            "ISSUE_MEMORY_SERVER_REQUIRE_OWNER_KEY", "0"
        ).strip().lower() not in {"0", "false", "no"}
        allow_synthetic_owner_key = os.environ.get(
            "ISSUE_MEMORY_SERVER_ALLOW_SYNTHETIC_OWNER_KEY", "0"
        ).strip().lower() not in {"0", "false", "no"}
        raw_max_instances = os.environ.get("ISSUE_MEMORY_MAX_MCP_INSTANCES", "").strip()
        compat_single_cap = os.environ.get(
            "ISSUE_MEMORY_ENFORCE_SINGLE_MCP_INSTANCE", "1"
        ).strip().lower() not in {"0", "false", "no"}
        if raw_max_instances:
            try:
                parsed_max_instances = int(raw_max_instances)
            except ValueError:
                max_mcp_instances = None if require_owner_key else 2
            else:
                max_mcp_instances = (
                    None if parsed_max_instances <= 0 else max(parsed_max_instances, 1)
                )
        else:
            max_mcp_instances = (
                None if require_owner_key else (1 if compat_single_cap else 2)
            )
        raw_duplicate_exit_code = os.environ.get(
            "ISSUE_MEMORY_SERVER_DUPLICATE_EXIT_CODE", ""
        ).strip()
        if raw_duplicate_exit_code:
            try:
                duplicate_exit_code = max(int(raw_duplicate_exit_code), 0)
            except ValueError:
                duplicate_exit_code = 75
        else:
            duplicate_exit_code = 75

        owner_key_env = (
            os.environ.get("ISSUE_MEMORY_MAIN_CONVERSATION_KEY_ENV", "").strip()
            or os.environ.get("ISSUE_MEMORY_SERVER_OWNER_KEY_ENV", "").strip()
            or os.environ.get("ISSUE_MEMORY_MCP_OWNER_KEY_ENV", "").strip()
        )

        server_enforce_parent_singleton = os.environ.get(
            "ISSUE_MEMORY_SERVER_ENFORCE_PARENT_SINGLETON", "0"
        ).strip().lower() not in {"0", "false", "no"}
        raw_idle_timeout = os.environ.get(
            "ISSUE_MEMORY_SERVER_PARENT_INSTANCE_IDLE_TIMEOUT_SECONDS", "0"
        ).strip()
        try:
            server_parent_instance_idle_timeout_seconds = max(
                int(raw_idle_timeout or "0"), 0
            )
        except ValueError:
            server_parent_instance_idle_timeout_seconds = 0
        raw_parent_monitor_interval = os.environ.get(
            "ISSUE_MEMORY_SERVER_PARENT_INSTANCE_MONITOR_INTERVAL_SECONDS", "1.0"
        ).strip()
        try:
            server_parent_instance_monitor_interval_seconds = max(
                float(raw_parent_monitor_interval or "1.0"), 0.2
            )
        except ValueError:
            server_parent_instance_monitor_interval_seconds = 1.0
        owner_key = ""
        owner_key_source = ""
        for env_name in (
            "ISSUE_MEMORY_MAIN_CONVERSATION_KEY",
            "ISSUE_MEMORY_SERVER_OWNER_KEY",
            "ISSUE_MEMORY_MCP_OWNER_KEY",
        ):
            candidate = os.environ.get(env_name, "").strip()
            if candidate:
                owner_key = candidate
                owner_key_source = env_name
                break
        if not owner_key and owner_key_env:
            owner_key = os.environ.get(owner_key_env, "").strip()
        elif (
            owner_key
            and not owner_key_env
            and owner_key_source == "ISSUE_MEMORY_MAIN_CONVERSATION_KEY"
        ):
            owner_key_env = owner_key_source
        current_codex_thread = os.environ.get("CODEX_THREAD_ID", "").strip()
        if not owner_key:
            derived_owner_key = _resolve_owner_key_from_codex_session_lineage(
                current_codex_thread
            )
            if derived_owner_key:
                owner_key = derived_owner_key
                owner_key_env = "CODEX_THREAD_ID"
        if not owner_key:
            inherited_owner_key, inherited_owner_key_env, inherited_codex_thread = (
                _resolve_owner_key_from_parent_process_lineage(owner_key_env)
            )
            if inherited_owner_key:
                owner_key = inherited_owner_key
                if inherited_owner_key_env:
                    owner_key_env = inherited_owner_key_env
                if not current_codex_thread and inherited_codex_thread:
                    current_codex_thread = inherited_codex_thread
        if not owner_key:
            inferred_owner_key, _inferred_codex_thread = (
                _resolve_owner_key_from_recent_codex_sessions()
            )
            if inferred_owner_key:
                owner_key = inferred_owner_key
                owner_key_env = "CODEX_THREAD_ID"
        if not owner_key and require_owner_key and allow_synthetic_owner_key:
            owner_key = _synthetic_process_owner_key()
            owner_key_env = "ISSUE_MEMORY_SYNTHETIC_OWNER_KEY"
        owner_role = (
            os.environ.get("ISSUE_MEMORY_MAIN_CONVERSATION_ROLE", "").strip()
            or os.environ.get("ISSUE_MEMORY_SERVER_OWNER_ROLE", "").strip()
            or os.environ.get("ISSUE_MEMORY_MCP_OWNER_ROLE", "").strip()
        )
        if (
            not owner_role
            and owner_key_env == "CODEX_THREAD_ID"
            and current_codex_thread
            and owner_key
        ):
            owner_role = "main" if current_codex_thread == owner_key else "subagent"
        if (
            not owner_role
            and owner_key_env == "ISSUE_MEMORY_SYNTHETIC_OWNER_KEY"
            and owner_key
        ):
            owner_role = "anonymous"

        settings = cls(
            issue_memory_home=home,
            db_path=db_path,
            state_dir=state_dir,
            log_dir=log_dir,
            backup_dir=backup_dir,
            windows_backup_target=windows_target,
            calibration_profile_path=calibration_profile_path,
            local_backup_keep=int(
                os.environ.get("ISSUE_MEMORY_LOCAL_BACKUP_KEEP", "30")
            ),
            mirror_backup_keep=int(
                os.environ.get("ISSUE_MEMORY_MIRROR_BACKUP_KEEP", "15")
            ),
            hostname=socket.gethostname(),
            default_user_scope=os.environ.get(
                "ISSUE_MEMORY_DEFAULT_USER_SCOPE", ""
            ).strip(),
            match_accept_threshold=float(
                os.environ.get("ISSUE_MEMORY_MATCH_ACCEPT_THRESHOLD", "0.68")
            ),
            match_weak_threshold=float(
                os.environ.get("ISSUE_MEMORY_MATCH_WEAK_THRESHOLD", "0.40")
            ),
            ambiguity_margin=float(
                os.environ.get("ISSUE_MEMORY_AMBIGUITY_MARGIN", "0.09")
            ),
            session_ttl_seconds=int(
                os.environ.get("ISSUE_MEMORY_SESSION_TTL_SECONDS", "21600")
            ),
            telemetry_enabled=os.environ.get("ISSUE_MEMORY_TELEMETRY_ENABLED", "1")
            .strip()
            .lower()
            not in {"0", "false", "no"},
            enable_dense_retrieval=os.environ.get(
                "ISSUE_MEMORY_ENABLE_DENSE_RETRIEVAL", "1"
            )
            .strip()
            .lower()
            not in {"0", "false", "no"},
            dense_embedding_dim=max(
                int(os.environ.get("ISSUE_MEMORY_DENSE_EMBEDDING_DIM", "192")), 32
            ),
            dense_candidate_limit=max(
                int(os.environ.get("ISSUE_MEMORY_DENSE_CANDIDATE_LIMIT", "16")), 4
            ),
            dense_similarity_floor=float(
                os.environ.get("ISSUE_MEMORY_DENSE_SIMILARITY_FLOOR", "0.12")
            ),
            dense_model_name=os.environ.get(
                "ISSUE_MEMORY_DENSE_MODEL_NAME", "hash-ngrams-v1"
            ).strip()
            or "hash-ngrams-v1",
            enable_strategy_bandit=os.environ.get(
                "ISSUE_MEMORY_ENABLE_STRATEGY_BANDIT", "0"
            )
            .strip()
            .lower()
            not in {"0", "false", "no"},
            enable_strategy_bandit_shadow_mode=os.environ.get(
                "ISSUE_MEMORY_ENABLE_STRATEGY_BANDIT_SHADOW_MODE", "0"
            )
            .strip()
            .lower()
            not in {"0", "false", "no"},
            strategy_overlay_scale=float(
                os.environ.get("ISSUE_MEMORY_STRATEGY_OVERLAY_SCALE", "0.20")
            ),
            variant_overlay_scale=float(
                os.environ.get("ISSUE_MEMORY_VARIANT_OVERLAY_SCALE", "0.08")
            ),
            safe_override_margin=float(
                os.environ.get("ISSUE_MEMORY_SAFE_OVERRIDE_MARGIN", "0.03")
            ),
            minimum_strategy_evidence=max(
                int(os.environ.get("ISSUE_MEMORY_MINIMUM_STRATEGY_EVIDENCE", "3")), 1
            ),
            strategy_half_life_days=max(
                int(os.environ.get("ISSUE_MEMORY_STRATEGY_HALF_LIFE_DAYS", "75")), 1
            ),
            variant_half_life_days=max(
                int(os.environ.get("ISSUE_MEMORY_VARIANT_HALF_LIFE_DAYS", "35")), 1
            ),
            enable_preference_rules=os.environ.get(
                "ISSUE_MEMORY_ENABLE_PREFERENCE_RULES", "1"
            )
            .strip()
            .lower()
            not in {"0", "false", "no"},
            preference_overlay_scale=float(
                os.environ.get("ISSUE_MEMORY_PREFERENCE_OVERLAY_SCALE", "1.0")
            ),
            max_preference_adjustment=float(
                os.environ.get("ISSUE_MEMORY_MAX_PREFERENCE_ADJUSTMENT", "0.18")
            ),
            guardrail_limit=max(
                int(os.environ.get("ISSUE_MEMORY_GUARDRAIL_LIMIT", "5")), 1
            ),
            telemetry_retention_days=max(
                int(os.environ.get("ISSUE_MEMORY_TELEMETRY_RETENTION_DAYS", "90")), 1
            ),
            resolved_review_retention_days=max(
                int(
                    os.environ.get("ISSUE_MEMORY_RESOLVED_REVIEW_RETENTION_DAYS", "120")
                ),
                1,
            ),
            enable_redaction=os.environ.get("ISSUE_MEMORY_ENABLE_REDACTION", "1")
            .strip()
            .lower()
            not in {"0", "false", "no"},
            enable_calibration_profile=os.environ.get(
                "ISSUE_MEMORY_ENABLE_CALIBRATION_PROFILE", "1"
            )
            .strip()
            .lower()
            not in {"0", "false", "no"},
            session_decay_half_life_minutes=max(
                float(
                    os.environ.get(
                        "ISSUE_MEMORY_SESSION_DECAY_HALF_LIFE_MINUTES", "30.0"
                    )
                ),
                1.0,
            ),
            implicit_feedback_timeout_minutes=max(
                int(
                    os.environ.get(
                        "ISSUE_MEMORY_IMPLICIT_FEEDBACK_TIMEOUT_MINUTES", "30"
                    )
                ),
                5,
            ),
            enable_cross_session_learning=os.environ.get(
                "ISSUE_MEMORY_ENABLE_CROSS_SESSION_LEARNING", "0"
            )
            .strip()
            .lower()
            not in {"0", "false", "no"},
            auto_rejection_threshold=max(
                int(os.environ.get("ISSUE_MEMORY_AUTO_REJECTION_THRESHOLD", "3")), 2
            ),
            max_mcp_instances=max_mcp_instances,
            server_lock_dir=server_lock_dir,
            server_duplicate_exit_code=duplicate_exit_code,
            server_enforce_parent_singleton=server_enforce_parent_singleton,
            server_parent_instance_idle_timeout_seconds=server_parent_instance_idle_timeout_seconds,
            server_parent_instance_monitor_interval_seconds=server_parent_instance_monitor_interval_seconds,
            server_require_owner_key=require_owner_key,
            server_owner_key=owner_key,
            server_owner_key_env=owner_key_env,
            server_owner_role=owner_role,
            fp_rate_alarm_threshold=max(
                float(os.environ.get("ISSUE_MEMORY_FP_RATE_ALARM_THRESHOLD", "0.15")),
                0.01,
            ),
            feedback_batch_window_seconds=max(
                int(
                    os.environ.get("ISSUE_MEMORY_FEEDBACK_BATCH_WINDOW_SECONDS", "300")
                ),
                0,
            ),
            feedback_batch_fp_review_threshold=max(
                int(
                    os.environ.get(
                        "ISSUE_MEMORY_FEEDBACK_BATCH_FP_REVIEW_THRESHOLD", "5"
                    )
                ),
                2,
            ),
            env_json_max_chars=max(
                int(os.environ.get("ISSUE_MEMORY_ENV_JSON_MAX_CHARS", "4000")), 256
            ),
            verification_output_max_chars=max(
                int(
                    os.environ.get("ISSUE_MEMORY_VERIFICATION_OUTPUT_MAX_CHARS", "4000")
                ),
                256,
            ),
            note_max_chars=max(
                int(os.environ.get("ISSUE_MEMORY_NOTE_MAX_CHARS", "2000")), 128
            ),
        )
        settings.ensure_dirs()
        return settings

    def ensure_dirs(self) -> None:
        """Create directories if they do not exist."""
        self.issue_memory_home.mkdir(parents=True, exist_ok=True)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        self.server_lock_dir.mkdir(parents=True, exist_ok=True)
        self.calibration_profile_path.parent.mkdir(parents=True, exist_ok=True)
        if self.windows_backup_target:
            self.windows_backup_target.mkdir(parents=True, exist_ok=True)


def _resolve_owner_key_from_codex_session_lineage(thread_id: str) -> str:
    """Resolve the root main-conversation thread id from CODEX session lineage."""

    thread_id = thread_id.strip()
    if not thread_id:
        return ""
    sessions_root = _codex_sessions_root()
    current = thread_id
    seen: set[str] = set()
    found_session_metadata = False
    while current and current not in seen:
        seen.add(current)
        payload = _load_codex_session_payload(current, sessions_root)
        if payload is None:
            return current if found_session_metadata else ""
        found_session_metadata = True
        parent = _codex_parent_thread_id(payload).strip()
        if not parent or parent == current:
            return current
        current = parent
    return ""


def _resolve_owner_key_from_parent_process_lineage(
    owner_key_env: str,
) -> tuple[str, str, str]:
    """Best-effort owner-key recovery from parent/ancestor process environments.

    Some MCP launchers replace the child environment instead of merging it with the
    parent environment. When that happens, the child can lose both
    `ISSUE_MEMORY_MAIN_CONVERSATION_KEY` and `CODEX_THREAD_ID`, which causes startup
    to fail before the MCP initialize response is sent. To keep current-process env
    precedence intact, this helper is used only after direct env resolution fails.
    """

    for env in _iter_parent_process_environments():
        owner_key_source = ""
        owner_key = ""
        for env_name in (
            "ISSUE_MEMORY_MAIN_CONVERSATION_KEY",
            "ISSUE_MEMORY_SERVER_OWNER_KEY",
            "ISSUE_MEMORY_MCP_OWNER_KEY",
        ):
            candidate = str(env.get(env_name, "")).strip()
            if candidate:
                owner_key_source = env_name
                owner_key = candidate
                break
        if not owner_key and owner_key_env:
            owner_key = str(env.get(owner_key_env, "")).strip()
        if owner_key:
            if owner_key_env:
                return owner_key, owner_key_env, ""
            if owner_key_source == "ISSUE_MEMORY_MAIN_CONVERSATION_KEY":
                return owner_key, owner_key_source, ""
            return owner_key, "", ""
        parent_codex_thread = str(env.get("CODEX_THREAD_ID", "")).strip()
        if not parent_codex_thread:
            continue
        derived_owner_key = _resolve_owner_key_from_codex_session_lineage(
            parent_codex_thread
        )
        if derived_owner_key:
            return derived_owner_key, "CODEX_THREAD_ID", parent_codex_thread
    return "", "", ""


def _resolve_owner_key_from_recent_codex_sessions(
    max_candidates: int = 32,
) -> tuple[str, str]:
    """Infer a single active owner key from the newest Codex session files.

    This is a last-resort fallback for MCP launch environments that expose neither
    the current child env nor readable ancestor envs. It only returns a value when
    the newest observed session files collapse to a single main-conversation root;
    otherwise it refuses to guess.
    """

    sessions_root = _codex_sessions_root()
    if not sessions_root.exists():
        return "", ""
    try:
        session_files = sorted(
            sessions_root.rglob("*.jsonl"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
    except OSError:
        return "", ""

    newest_thread_id = ""
    resolved_owner_keys: set[str] = set()
    for path in session_files[: max(max_candidates, 1)]:
        payload = _read_codex_session_payload(path)
        if not isinstance(payload, dict):
            continue
        thread_id = str(payload.get("id") or "").strip()
        if not thread_id:
            continue
        if not newest_thread_id:
            newest_thread_id = thread_id
        owner_key = _resolve_owner_key_from_codex_session_lineage(thread_id)
        if owner_key:
            resolved_owner_keys.add(owner_key)
        if len(resolved_owner_keys) > 1:
            return "", ""
    if len(resolved_owner_keys) != 1:
        return "", ""
    return next(iter(resolved_owner_keys)), newest_thread_id


def _synthetic_process_owner_key() -> str:
    """Create a unique process-scoped owner key for contextless launcher fallbacks."""

    return f"synthetic-process-{os.getppid()}-{os.getpid()}"


def _iter_parent_process_environments(max_depth: int = 32) -> list[dict[str, str]]:
    """Return ancestor process env snapshots when readable on the current platform."""

    envs: list[dict[str, str]] = []
    current_pid = os.getppid()
    seen: set[int] = set()
    depth = 0
    while current_pid > 1 and current_pid not in seen and depth < max_depth:
        seen.add(current_pid)
        env = _read_process_environment(current_pid)
        if env:
            envs.append(env)
        next_pid = _read_parent_pid(current_pid)
        if next_pid <= 0 or next_pid == current_pid:
            break
        current_pid = next_pid
        depth += 1
    return envs


def _read_process_environment(pid: int) -> dict[str, str]:
    """Read `/proc/<pid>/environ` as a decoded environment map."""

    if pid <= 1:
        return {}
    path = Path("/proc") / str(pid) / "environ"
    try:
        raw = path.read_bytes()
    except OSError:
        return {}
    env: dict[str, str] = {}
    for chunk in raw.split(b"\0"):
        if not chunk or b"=" not in chunk:
            continue
        key_bytes, value_bytes = chunk.split(b"=", 1)
        key = key_bytes.decode("utf-8", errors="ignore").strip()
        if not key:
            continue
        env[key] = value_bytes.decode("utf-8", errors="ignore").strip()
    return env


def _read_parent_pid(pid: int) -> int:
    """Read the parent pid for a process from `/proc/<pid>/status`."""

    if pid <= 1:
        return 0
    path = Path("/proc") / str(pid) / "status"
    try:
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            if not line.startswith("PPid:"):
                continue
            _, _, raw_ppid = line.partition(":")
            return int(raw_ppid.strip() or "0")
    except (OSError, ValueError):
        return 0
    return 0


def _codex_sessions_root() -> Path:
    codex_home = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")).expanduser()
    return codex_home / "sessions"


def _load_codex_session_payload(
    thread_id: str, sessions_root: Path
) -> dict[str, object] | None:
    session_path = _find_codex_session_file(thread_id, sessions_root)
    if session_path is None:
        return None
    return _read_codex_session_payload(session_path)


def _read_codex_session_payload(session_path: Path) -> dict[str, object] | None:
    try:
        first_line = session_path.read_text(
            encoding="utf-8", errors="ignore"
        ).splitlines()[0]
        record = json.loads(first_line)
    except (IndexError, OSError, ValueError, TypeError):
        return None
    payload = record.get("payload", {})
    return payload if isinstance(payload, dict) else None


def _find_codex_session_file(thread_id: str, sessions_root: Path) -> Path | None:
    if not sessions_root.exists():
        return None
    candidates: list[Path] = []
    for path in sessions_root.rglob("*.jsonl"):
        if thread_id not in path.name:
            continue
        payload = _read_codex_session_payload(path)
        if not isinstance(payload, dict):
            continue
        if str(payload.get("id") or "").strip() == thread_id:
            candidates.append(path)
    if not candidates:
        return None
    candidates.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    return candidates[0]


def _codex_parent_thread_id(payload: dict[str, object]) -> str:
    forked_from_id = payload.get("forked_from_id")
    if isinstance(forked_from_id, str) and forked_from_id.strip():
        return forked_from_id
    source = payload.get("source")
    if not isinstance(source, dict):
        return ""
    subagent = source.get("subagent")
    if not isinstance(subagent, dict):
        return ""
    thread_spawn = subagent.get("thread_spawn")
    if not isinstance(thread_spawn, dict):
        return ""
    parent_thread_id = thread_spawn.get("parent_thread_id")
    return parent_thread_id.strip() if isinstance(parent_thread_id, str) else ""
