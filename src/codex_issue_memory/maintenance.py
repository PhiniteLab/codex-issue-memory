from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
import os
import re
from pathlib import Path
import tempfile
from typing import Any, Callable

try:
    import tomllib  # type: ignore[attr-defined]
except ImportError:  # pragma: no cover - Python < 3.11
    tomllib = None  # type: ignore[assignment]

from .app import IssueMemoryApp
from .backup import BackupManager
from .benchmarks import (
    run_dense_bandit_benchmark,
    run_failure_taxonomy_benchmark,
    run_hard_negative_benchmark,
    run_merge_correctness_stress,
    run_real_world_eval,
    run_runtime_diagnostics,
    run_threshold_calibration,
    run_feedback_driven_calibration,
    run_user_domain_benchmark,
    seed_dense_bandit_memory,
    seed_hard_negative_memory,
    seed_real_world_memory,
    seed_user_domain_memory,
)
from .e2e_mcp_reuse_harness import (
    harness_succeeded as e2e_mcp_reuse_harness_succeeded,
    render_human as render_e2e_mcp_reuse_harness,
    run_harness as run_e2e_mcp_reuse_harness,
)
from .lifecycle import read_server_lifecycle_status
from .settings import Settings
from .storage import IssueMemoryStore


_ENV_KEYS = (
    "ISSUE_MEMORY_HOME",
    "ISSUE_MEMORY_DB_PATH",
    "ISSUE_MEMORY_STATE_DIR",
    "ISSUE_MEMORY_BACKUP_DIR",
    "ISSUE_MEMORY_LOG_DIR",
)


def cmd_init_db() -> None:
    store = IssueMemoryStore.from_env()
    store.initialize()
    print(
        json.dumps(
            {
                "status": "ok",
                "db_path": str(store.settings.db_path),
                "schema": store.schema_state().to_dict(),
            },
            indent=2,
        )
    )


def cmd_migrate_v2() -> None:
    store = IssueMemoryStore.from_env()
    state = store.migrate()
    print(
        json.dumps(
            {
                "status": "ok",
                "db_path": str(store.settings.db_path),
                "schema": state.to_dict(),
            },
            indent=2,
        )
    )


def cmd_schema_version() -> None:
    store = IssueMemoryStore.from_env()
    store.initialize()
    print(
        json.dumps({"status": "ok", "schema": store.schema_state().to_dict()}, indent=2)
    )


def cmd_backup() -> None:
    manager = BackupManager.from_env()
    result = manager.create_backup()
    print(json.dumps(result.to_dict(), indent=2))


def cmd_list_backups(limit: int) -> None:
    manager = BackupManager.from_env()
    print(json.dumps({"backups": manager.list_backups(limit=limit)}, indent=2))


def cmd_verify_backup(path: str) -> None:
    manager = BackupManager.from_env()
    print(json.dumps(manager.verify_backup(path), indent=2))


def cmd_restore_backup(path: str, *, create_safety_backup: bool) -> None:
    manager = BackupManager.from_env()
    print(
        json.dumps(
            manager.restore_backup(path, create_safety_backup=create_safety_backup),
            indent=2,
        )
    )


def cmd_metrics(window_days: int) -> None:
    app = IssueMemoryApp()
    print(json.dumps(app.issue_metrics(window_days=window_days), indent=2))


def cmd_server_status() -> None:
    settings = Settings.from_env()
    print(json.dumps(read_server_lifecycle_status(settings).to_dict(), indent=2))


def _recommended_env(
    *, settings: Settings, mode: str, max_instances: int
) -> dict[str, str]:
    if mode not in {"shadow", "active", "single"}:
        raise ValueError(f"unsupported rollout mode: {mode}")
    resolved_max = (
        1
        if mode == "single"
        else (None if int(max_instances) <= 0 else max(int(max_instances), 1))
    )
    env = {
        "ISSUE_MEMORY_HOME": str(settings.issue_memory_home),
        "ISSUE_MEMORY_DB_PATH": str(settings.db_path),
        "ISSUE_MEMORY_STATE_DIR": str(settings.state_dir),
        "ISSUE_MEMORY_BACKUP_DIR": str(settings.backup_dir),
        "ISSUE_MEMORY_LOG_DIR": str(settings.log_dir),
        "ISSUE_MEMORY_SERVER_LOCK_DIR": str(settings.server_lock_dir),
        "ISSUE_MEMORY_SERVER_DUPLICATE_EXIT_CODE": str(
            settings.server_duplicate_exit_code
        ),
        "ISSUE_MEMORY_SERVER_REQUIRE_OWNER_KEY": "1" if resolved_max is None else "0",
        "ISSUE_MEMORY_SERVER_OWNER_KEY_ENV": "ISSUE_MEMORY_MAIN_CONVERSATION_KEY",
        "ISSUE_MEMORY_SERVER_ALLOW_SYNTHETIC_OWNER_KEY": "1",
        "ISSUE_MEMORY_ENFORCE_SINGLE_MCP_INSTANCE": "0"
        if resolved_max is None or resolved_max > 1
        else "1",
        "ISSUE_MEMORY_MAX_MCP_INSTANCES": "0"
        if resolved_max is None
        else str(resolved_max),
        "ISSUE_MEMORY_SERVER_ENFORCE_PARENT_SINGLETON": "1",
        "ISSUE_MEMORY_SERVER_PARENT_INSTANCE_IDLE_TIMEOUT_SECONDS": "0",
        "ISSUE_MEMORY_SERVER_PARENT_INSTANCE_MONITOR_INTERVAL_SECONDS": "1.0",
        "ISSUE_MEMORY_ENABLE_STRATEGY_BANDIT": "1",
        "ISSUE_MEMORY_ENABLE_STRATEGY_BANDIT_SHADOW_MODE": "1"
        if mode == "shadow"
        else "0",
        "ISSUE_MEMORY_ENABLE_PREFERENCE_RULES": "1",
        "ISSUE_MEMORY_ENABLE_REDACTION": "1",
        "ISSUE_MEMORY_ENABLE_CALIBRATION_PROFILE": "1",
        "ISSUE_MEMORY_CALIBRATION_PROFILE_PATH": str(settings.calibration_profile_path),
        "ISSUE_MEMORY_MATCH_ACCEPT_THRESHOLD": f"{settings.match_accept_threshold:.2f}",
        "ISSUE_MEMORY_MATCH_WEAK_THRESHOLD": f"{settings.match_weak_threshold:.2f}",
        "ISSUE_MEMORY_AMBIGUITY_MARGIN": f"{settings.ambiguity_margin:.2f}",
        "ISSUE_MEMORY_DEFAULT_USER_SCOPE": settings.default_user_scope,
    }
    return env


def _format_env_block(env: dict[str, str], *, fmt: str) -> str:
    if fmt == "json":
        return json.dumps(env, indent=2, ensure_ascii=False, sort_keys=True)
    if fmt == "env":
        return "\n".join(
            f"export {key}={json.dumps(value)}" for key, value in env.items()
        )
    if fmt == "toml":
        lines = ["[mcp_servers.issue_memory.env]"]
        lines.extend(f"{key} = {json.dumps(value)}" for key, value in env.items())
        return "\n".join(lines)
    raise ValueError(f"unsupported format: {fmt}")


def cmd_recommended_config(mode: str, fmt: str, max_instances: int) -> None:
    settings = Settings.from_env()
    env = _recommended_env(settings=settings, mode=mode, max_instances=max_instances)
    rendered_max = env["ISSUE_MEMORY_MAX_MCP_INSTANCES"]
    payload = {
        "mode": mode,
        "max_instances": None if rendered_max == "0" else int(rendered_max),
        "format": fmt,
        "env": env,
        "rendered": _format_env_block(env, fmt=fmt),
    }
    print(json.dumps(payload, indent=2, ensure_ascii=False))


def _load_codex_config_metadata(codex_home: Path) -> dict[str, Any]:
    config_path = codex_home / "config.toml"
    if not config_path.exists():
        return {
            "config_path": str(config_path),
            "exists": False,
            "block_count": 0,
            "env": {},
            "parse_error": None,
        }
    raw = config_path.read_text(encoding="utf-8")
    block_count = len(
        re.findall(r"^\[mcp_servers\.issue_memory\]\s*$", raw, flags=re.MULTILINE)
    )
    env: dict[str, Any] = {}
    parse_error: str | None = None
    if tomllib is not None:
        try:
            parsed = tomllib.loads(raw)
            env = dict(
                (parsed.get("mcp_servers", {}).get("issue_memory", {}) or {}).get(
                    "env", {}
                )
                or {}
            )
        except tomllib.TOMLDecodeError as exc:  # type: ignore[union-attr]
            parse_error = str(exc)
    else:
        env_match = re.search(
            r'\[mcp_servers\.issue_memory\.env\]\s*(?P<body>(?:[A-Z0-9_]+\s*=\s*"[^"]*"\s*)+)',
            raw,
            flags=re.MULTILINE,
        )
        if env_match is None:
            env_match = re.search(
                r"\[mcp_servers\.issue_memory\].*?env\s*=\s*\{(?P<body>.*?)\}\s*$",
                raw,
                flags=re.DOTALL | re.MULTILINE,
            )
        if env_match is not None:
            body = env_match.group("body")
            for key, value in re.findall(r'([A-Z0-9_]+)\s*=\s*"([^"]*)"', body):
                env[key] = value
        else:
            parse_error = "unable to parse issue_memory env block without tomllib"
    return {
        "config_path": str(config_path),
        "exists": True,
        "block_count": block_count,
        "env": env,
        "parse_error": parse_error,
    }


def _check(
    name: str, ok: bool, detail: str, *, severity: str = "error"
) -> dict[str, Any]:
    return {"name": name, "ok": bool(ok), "severity": severity, "detail": detail}


def cmd_doctor(mode: str, max_instances: int, codex_home: str | None) -> None:
    settings = Settings.from_env()
    expected_env = _recommended_env(
        settings=settings, mode=mode, max_instances=max_instances
    )
    lifecycle = read_server_lifecycle_status(settings).to_dict()
    codex_path = Path(codex_home).expanduser() if codex_home else Path.home() / ".codex"
    config_meta = _load_codex_config_metadata(codex_path)
    checks: list[dict[str, Any]] = []
    checks.append(
        _check(
            "codex-config-exists",
            bool(config_meta["exists"]),
            "config.toml found"
            if config_meta["exists"]
            else f"missing: {config_meta['config_path']}",
        )
    )
    if config_meta["exists"]:
        checks.append(
            _check(
                "single-issue-memory-block",
                int(config_meta["block_count"]) == 1,
                f"found {config_meta['block_count']} [mcp_servers.issue_memory] block(s)",
            )
        )
        checks.append(
            _check(
                "config-parse",
                config_meta["parse_error"] is None,
                "parsed successfully"
                if config_meta["parse_error"] is None
                else str(config_meta["parse_error"]),
            )
        )
    env = config_meta.get("env", {}) or {}
    if env:
        for key in (
            "ISSUE_MEMORY_SERVER_LOCK_DIR",
            "ISSUE_MEMORY_SERVER_DUPLICATE_EXIT_CODE",
            "ISSUE_MEMORY_SERVER_REQUIRE_OWNER_KEY",
            "ISSUE_MEMORY_SERVER_OWNER_KEY_ENV",
            "ISSUE_MEMORY_SERVER_ALLOW_SYNTHETIC_OWNER_KEY",
            "ISSUE_MEMORY_MAX_MCP_INSTANCES",
            "ISSUE_MEMORY_SERVER_ENFORCE_PARENT_SINGLETON",
            "ISSUE_MEMORY_SERVER_PARENT_INSTANCE_IDLE_TIMEOUT_SECONDS",
            "ISSUE_MEMORY_SERVER_PARENT_INSTANCE_MONITOR_INTERVAL_SECONDS",
            "ISSUE_MEMORY_ENABLE_STRATEGY_BANDIT",
            "ISSUE_MEMORY_ENABLE_STRATEGY_BANDIT_SHADOW_MODE",
            "ISSUE_MEMORY_ENABLE_PREFERENCE_RULES",
            "ISSUE_MEMORY_ENABLE_REDACTION",
        ):
            expected = expected_env.get(key, "")
            actual = str(env.get(key, ""))
            checks.append(
                _check(
                    f"env:{key}",
                    actual == expected,
                    f"actual={actual!r}, expected={expected!r}",
                    severity="warning" if key.endswith("SHADOW_MODE") else "error",
                )
            )
    expected_cap_raw = str(expected_env["ISSUE_MEMORY_MAX_MCP_INSTANCES"])
    if expected_cap_raw == "0":
        checks.append(
            _check(
                "owner-key-required",
                str(env.get("ISSUE_MEMORY_SERVER_REQUIRE_OWNER_KEY", "")) == "1",
                f"actual={env.get('ISSUE_MEMORY_SERVER_REQUIRE_OWNER_KEY', '')!r}, expected='1'",
            )
        )
        active_slots = lifecycle.get("active_slots", []) or []
        ownerless = [
            slot.get("pid")
            for slot in active_slots
            if not str(slot.get("owner_key") or "").strip()
        ]
        checks.append(
            _check(
                "active-slots-have-owner-keys",
                not ownerless,
                "all active slots have owner keys"
                if not ownerless
                else f"ownerless_pids={ownerless}",
                severity="warning",
            )
        )
    else:
        checks.append(
            _check(
                "instance-cap",
                int(lifecycle.get("active_count", 0) or 0) <= int(expected_cap_raw),
                f"active_count={lifecycle.get('active_count', 0)}, max_instances={expected_cap_raw}",
            )
        )
    checks.append(
        _check(
            "paths-exist",
            settings.issue_memory_home.exists()
            and settings.state_dir.exists()
            and settings.backup_dir.exists(),
            f"home={settings.issue_memory_home}, state={settings.state_dir}, backup={settings.backup_dir}",
        )
    )
    checks.append(
        _check(
            "calibration-profile",
            settings.calibration_profile_path.exists(),
            f"path={settings.calibration_profile_path}",
            severity="warning",
        )
    )
    backup_manager = BackupManager.from_env()
    backups = backup_manager.list_backups(limit=1)
    if backups:
        created_raw = backups[0].get("created_at_utc") or backups[0].get("created_at")
        fresh = False
        if created_raw:
            try:
                created_at = datetime.fromisoformat(
                    str(created_raw).replace("Z", "+00:00")
                )
                fresh = created_at >= datetime.now(timezone.utc) - timedelta(days=7)
            except ValueError:
                fresh = False
        checks.append(
            _check(
                "backup-freshness",
                fresh,
                f"latest_backup={created_raw}",
                severity="warning",
            )
        )
    else:
        checks.append(
            _check("backup-freshness", False, "no backups found", severity="warning")
        )

    # 5.3: FP rate degradation alarm
    try:
        store = IssueMemoryStore.from_env()
        store.initialize()
        metrics = store.metrics_summary(window_days=30)
        fp_rate = float(metrics.get("feedback", {}).get("false_positive_rate", 0.0))
        fp_threshold = float(settings.fp_rate_alarm_threshold)
        checks.append(
            _check(
                "fp-rate-alarm",
                fp_rate < fp_threshold,
                f"fp_rate={fp_rate:.4f}, threshold={fp_threshold:.2f}",
                severity="warning",
            )
        )
    except Exception:
        checks.append(
            _check(
                "fp-rate-alarm",
                True,
                "unable to compute FP rate (no data)",
                severity="warning",
            )
        )

    error_count = sum(
        1 for item in checks if not item["ok"] and item["severity"] == "error"
    )
    warning_count = sum(
        1 for item in checks if not item["ok"] and item["severity"] != "error"
    )
    status = (
        "ok"
        if error_count == 0 and warning_count == 0
        else ("warn" if error_count == 0 else "fail")
    )
    payload = {
        "status": status,
        "mode": mode,
        "expected_max_instances": None
        if expected_cap_raw == "0"
        else int(expected_cap_raw),
        "codex_home": str(codex_path),
        "server_status": lifecycle,
        "checks": checks,
        "summary": {"errors": error_count, "warnings": warning_count},
        "recommended_env": expected_env,
    }
    print(json.dumps(payload, indent=2, ensure_ascii=False))


def cmd_prune_retention(telemetry_days: int, review_days: int) -> None:
    store = IssueMemoryStore.from_env()
    store.initialize()
    result = store.prune_operational_data(
        telemetry_retention_days=telemetry_days,
        resolved_review_retention_days=review_days,
    )
    print(json.dumps(result, indent=2))


def cmd_review_queue(status: str, limit: int) -> None:
    app = IssueMemoryApp()
    print(json.dumps(app.issue_review_queue(status=status, limit=limit), indent=2))


def cmd_resolve_review(review_id: int, decision: str, note: str) -> None:
    app = IssueMemoryApp()
    print(
        json.dumps(
            app.issue_review_resolve(review_id=review_id, decision=decision, note=note),
            indent=2,
        )
    )


def _configure_temp_environment(temp_dir: str) -> None:
    base = Path(temp_dir)
    os.environ["ISSUE_MEMORY_HOME"] = str(base / "share")
    os.environ["ISSUE_MEMORY_DB_PATH"] = str(base / "share" / "issue_memory.sqlite3")
    os.environ["ISSUE_MEMORY_STATE_DIR"] = str(base / "state")
    os.environ["ISSUE_MEMORY_BACKUP_DIR"] = str(base / "share" / "backups")
    os.environ["ISSUE_MEMORY_LOG_DIR"] = str(base / "state" / "log")


def _with_temp_issue_memory(
    fn: Callable[[IssueMemoryApp], dict[str, Any]],
) -> dict[str, Any]:
    env_backup = {key: os.environ.get(key) for key in _ENV_KEYS}
    try:
        with tempfile.TemporaryDirectory(prefix="issue-memory-maint-") as temp_dir:
            _configure_temp_environment(temp_dir)
            app = IssueMemoryApp()
            return fn(app)
    finally:
        for key, value in env_backup.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _persist_report(name: str, payload: dict[str, Any]) -> dict[str, Any]:
    store = IssueMemoryStore.from_env()
    store.initialize()
    report_path = store.save_report(name, payload)
    payload = dict(payload)
    payload["report_path"] = str(report_path)
    return payload


def cmd_smoke() -> None:
    def runner(app: IssueMemoryApp) -> dict[str, Any]:
        app.issue_record_resolution(
            title="Relative sqlite path breaks outside repo root",
            raw_error="FileNotFoundError: references/contractsDatabase.sqlite3",
            canonical_fix="Resolve the SQLite path relative to the module file instead of runtime cwd.",
            prevention_rule="No production file path may depend on runtime cwd.",
            project_scope="global",
            canonical_symptom="sqlite database path fails when the module is run from outside the repository root",
            verification_steps="Run the failing module from the repo root and from an external cwd.",
            tags="sqlite,path,cwd,repo-root",
            error_family="sqlite_error",
            root_cause_class="cwd_relative_path_bug",
            command="python -m app.main",
            file_path="services/db_loader.py",
            domain="python",
        )

        result = app.issue_match(
            error_text="FileNotFoundError: config/contractsDatabase.sqlite3 while running python -m app.main from another directory",
            command="python -m app.main",
            file_path="services/db_loader.py",
            project_scope="global",
            limit=3,
        )
        matches = result["matches"]
        assert matches, "Expected at least one match from smoke test"
        assert matches[0]["pattern_id"] >= 1, "Expected a valid pattern id"
        assert result["retrieval_event_id"] is not None, "Expected telemetry event id"
        return {
            "status": "ok",
            "top_match": matches[0],
            "retrieval_event_id": result["retrieval_event_id"],
        }

    print(json.dumps(_with_temp_issue_memory(runner), indent=2))


def cmd_smoke_learning() -> None:
    def runner(app: IssueMemoryApp) -> dict[str, Any]:
        app.issue_record_resolution(
            title="Requests missing in API worker",
            raw_error="ModuleNotFoundError: No module named requests while starting API worker",
            canonical_fix="Install requests into the active environment used by the API worker.",
            prevention_rule="Pin and install runtime dependencies in the worker environment.",
            project_scope="global",
            canonical_symptom="requests import fails during api worker startup",
            verification_steps="Run the API worker import check inside the same environment.",
            tags="python,import,requests,api",
            error_family="import_error",
            root_cause_class="missing_python_module",
            command="python worker.py",
            file_path="api/worker.py",
            domain="python",
        )
        app.issue_record_resolution(
            title="Requests missing in CLI utility",
            raw_error="ImportError: cannot import name requests from CLI utility bootstrap",
            canonical_fix="Install requests into the environment used by the CLI entrypoint.",
            prevention_rule="Keep CLI dependencies synchronized with runtime requirements.",
            project_scope="global",
            canonical_symptom="requests import fails during cli bootstrap",
            verification_steps="Run the CLI import check inside the same environment.",
            tags="python,import,requests,cli",
            error_family="import_error",
            root_cause_class="missing_python_module",
            command="python cli.py",
            file_path="cli/bootstrap.py",
            domain="python",
        )

        first = app.issue_match(
            error_text="ModuleNotFoundError: No module named requests",
            project_scope="global",
            session_id="smoke-session",
            limit=3,
        )
        assert first["decision"]["status"] == "ambiguous", (
            "Expected close candidates before feedback"
        )
        first_top = first["matches"][0]["pattern_id"]

        feedback = app.issue_feedback(
            retrieval_event_id=int(first["retrieval_event_id"]),
            feedback_type="candidate_rejected",
            candidate_rank=1,
            notes="Synthetic smoke rejection",
        )
        assert feedback["resolved_candidate"]["pattern_id"] == first_top, (
            "Feedback resolved wrong top candidate"
        )

        second = app.issue_match(
            error_text="ModuleNotFoundError: No module named requests",
            project_scope="global",
            session_id="smoke-session",
            limit=3,
        )
        assert second["matches"][0]["pattern_id"] != first_top, (
            "Rejected candidate should be demoted within session"
        )

        verify = app.issue_feedback(
            retrieval_event_id=int(second["retrieval_event_id"]),
            feedback_type="fix_verified",
            candidate_rank=1,
            notes="Synthetic smoke verification",
        )
        snapshot = app.session_service.snapshot("smoke-session")
        return {
            "status": "ok",
            "initial_top_pattern_id": first_top,
            "reranked_top_pattern_id": second["matches"][0]["pattern_id"],
            "retrieval_event_ids": [
                first["retrieval_event_id"],
                second["retrieval_event_id"],
            ],
            "session_memory": snapshot,
            "last_learning_update": verify["learning"],
        }

    print(json.dumps(_with_temp_issue_memory(runner), indent=2))


def cmd_benchmark_user_domains() -> None:
    def runner(app: IssueMemoryApp) -> dict[str, Any]:
        seed_user_domain_memory(app)
        return run_user_domain_benchmark(app, repeats=20)

    print(
        json.dumps(
            _persist_report("benchmark_user_domains", _with_temp_issue_memory(runner)),
            indent=2,
            ensure_ascii=False,
        )
    )


def cmd_benchmark_failure_taxonomy() -> None:
    def runner(app: IssueMemoryApp) -> dict[str, Any]:
        seed_user_domain_memory(app)
        return run_failure_taxonomy_benchmark(app, repeats=10)

    print(
        json.dumps(
            _persist_report(
                "benchmark_failure_taxonomy", _with_temp_issue_memory(runner)
            ),
            indent=2,
            ensure_ascii=False,
        )
    )


def cmd_runtime_diagnostics() -> None:
    def runner(app: IssueMemoryApp) -> dict[str, Any]:
        return run_runtime_diagnostics(app, repeats=8)

    print(
        json.dumps(
            _persist_report("runtime_diagnostics", _with_temp_issue_memory(runner)),
            indent=2,
            ensure_ascii=False,
        )
    )


def cmd_e2e_mcp_reuse_harness(timeout: float, *, json_output: bool) -> None:
    payload = run_e2e_mcp_reuse_harness(timeout=timeout)
    if json_output:
        print(json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True))
    else:
        print(render_e2e_mcp_reuse_harness(payload))
    if not e2e_mcp_reuse_harness_succeeded(payload):
        raise SystemExit(1)


def cmd_benchmark_dense_bandit() -> None:
    def runner(app: IssueMemoryApp) -> dict[str, Any]:
        seed_dense_bandit_memory(app)
        return run_dense_bandit_benchmark(app, repeats=8)

    print(
        json.dumps(
            _persist_report("benchmark_dense_bandit", _with_temp_issue_memory(runner)),
            indent=2,
            ensure_ascii=False,
        )
    )


def cmd_benchmark_real_world() -> None:
    def runner(app: IssueMemoryApp) -> dict[str, Any]:
        seed_real_world_memory(app)
        return run_real_world_eval(app, repeats=1)

    print(
        json.dumps(
            _persist_report(
                "benchmark_real_world_eval", _with_temp_issue_memory(runner)
            ),
            indent=2,
            ensure_ascii=False,
        )
    )


def cmd_benchmark_hard_negatives() -> None:
    def runner(app: IssueMemoryApp) -> dict[str, Any]:
        seed_hard_negative_memory(app)
        return run_hard_negative_benchmark(app, repeats=1)

    print(
        json.dumps(
            _persist_report(
                "benchmark_hard_negatives", _with_temp_issue_memory(runner)
            ),
            indent=2,
            ensure_ascii=False,
        )
    )


def cmd_benchmark_merge_stress() -> None:
    def runner(app: IssueMemoryApp) -> dict[str, Any]:
        return run_merge_correctness_stress(app)

    print(
        json.dumps(
            _persist_report(
                "benchmark_merge_correctness_stress", _with_temp_issue_memory(runner)
            ),
            indent=2,
            ensure_ascii=False,
        )
    )


def evaluate_benchmark_gate_reports(
    reports: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Evaluate benchmark reports against conservative release-gate thresholds."""

    checks: list[dict[str, Any]] = []

    def add(name: str, ok: bool, detail: str) -> None:
        checks.append({"name": name, "ok": bool(ok), "detail": detail})

    hard = reports.get("hard_negatives")
    if not isinstance(hard, dict) or not hard:
        add("hard_negatives_report_present", False, "required report missing")
    else:
        positive_top1 = float(hard.get("positive_top1_accuracy", 0.0))
        unsafe_rate = float(hard.get("unsafe_clear_match_rate", 1.0))
        add(
            "hard_negatives_positive_top1",
            positive_top1 >= 0.60,
            f"positive_top1_accuracy={positive_top1:.4f} threshold>=0.60",
        )
        add(
            "hard_negatives_unsafe_clear_match_rate",
            unsafe_rate <= 0.50,
            f"unsafe_clear_match_rate={unsafe_rate:.4f} threshold<=0.50",
        )

    merge = reports.get("merge_stress")
    if not isinstance(merge, dict) or not merge:
        add("merge_stress_report_present", False, "required report missing")
    else:
        catastrophic = int(merge.get("catastrophic_variant_merge_count", 999999))
        success_rate = float(merge.get("success_rate", 0.0))
        add(
            "merge_stress_no_catastrophic_variant_merge",
            catastrophic == 0,
            f"catastrophic_variant_merge_count={catastrophic} threshold=0",
        )
        add(
            "merge_stress_success_rate",
            success_rate >= 0.75,
            f"success_rate={success_rate:.4f} threshold>=0.75",
        )

    real_world = reports.get("real_world", {})
    if real_world:
        top1 = float(real_world.get("top1_accuracy", 0.0))
        precision = float(real_world.get("clear_match_precision", 0.0))
        negative_safety = float(real_world.get("negative_safety_rate", 0.0))
        add("real_world_top1", top1 >= 0.80, f"top1_accuracy={top1:.4f} threshold>=0.80")
        add(
            "real_world_clear_precision",
            precision >= 0.80,
            f"clear_match_precision={precision:.4f} threshold>=0.80",
        )
        add(
            "real_world_negative_safety",
            negative_safety >= 0.70,
            f"negative_safety_rate={negative_safety:.4f} threshold>=0.70",
        )

    dense = reports.get("dense_bandit", {})
    if dense:
        dense_top1 = float(dense.get("dense_top1_accuracy", 0.0))
        add(
            "dense_bandit_top1",
            dense_top1 >= 0.75,
            f"dense_top1_accuracy={dense_top1:.4f} threshold>=0.75",
        )

    user = reports.get("user_domains", {})
    if user:
        user_top1 = float(user.get("top1_accuracy", 0.0))
        failures = user.get("failures", [])
        add(
            "user_domains_top1",
            user_top1 >= 0.95,
            f"top1_accuracy={user_top1:.4f} threshold>=0.95",
        )
        add(
            "user_domains_no_failures",
            not failures,
            f"failure_count={len(failures) if isinstance(failures, list) else 'unknown'} threshold=0",
        )

    failed = [check for check in checks if not check["ok"]]
    return {
        "status": "ok" if not failed else "failed",
        "checks": checks,
        "summary": {"total": len(checks), "failed": len(failed)},
    }


def cmd_quality_gate_benchmarks(*, full: bool = False) -> None:
    """Run benchmark smoke gates and fail fast for unsafe retrieval regressions."""

    def run_hard(app: IssueMemoryApp) -> dict[str, Any]:
        seed_hard_negative_memory(app)
        return run_hard_negative_benchmark(app, repeats=1)

    def run_merge(app: IssueMemoryApp) -> dict[str, Any]:
        return run_merge_correctness_stress(app)

    reports = {
        "hard_negatives": _with_temp_issue_memory(run_hard),
        "merge_stress": _with_temp_issue_memory(run_merge),
    }
    if full:
        def run_real(app: IssueMemoryApp) -> dict[str, Any]:
            seed_real_world_memory(app)
            return run_real_world_eval(app, repeats=1)

        def run_dense(app: IssueMemoryApp) -> dict[str, Any]:
            seed_dense_bandit_memory(app)
            return run_dense_bandit_benchmark(app, repeats=4)

        def run_user(app: IssueMemoryApp) -> dict[str, Any]:
            seed_user_domain_memory(app)
            return run_user_domain_benchmark(app, repeats=2)

        reports["real_world"] = _with_temp_issue_memory(run_real)
        reports["dense_bandit"] = _with_temp_issue_memory(run_dense)
        reports["user_domains"] = _with_temp_issue_memory(run_user)

    gate = evaluate_benchmark_gate_reports(reports)
    payload = _persist_report(
        "quality_gate_benchmarks",
        {"status": gate["status"], "full": bool(full), "reports": reports, "gate": gate},
    )
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    if gate["status"] != "ok":
        raise SystemExit(1)


def cmd_calibrate_thresholds(write_profile: bool, from_feedback: bool = False) -> None:
    if from_feedback:
        store = IssueMemoryStore.from_env()
        store.initialize()
        report = run_feedback_driven_calibration(store)
        report = _persist_report("threshold_calibration_feedback", report)
        if write_profile and report.get("global"):
            profile_payload = {
                key: report[key]
                for key in ("version", "generated_at", "global", "families", "metrics")
                if key in report
            }
            store.settings.calibration_profile_path.write_text(
                json.dumps(
                    profile_payload, indent=2, ensure_ascii=False, sort_keys=True
                ),
                encoding="utf-8",
            )
            report["calibration_profile_path"] = str(
                store.settings.calibration_profile_path
            )
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return

    def runner(app: IssueMemoryApp) -> dict[str, Any]:
        seed_real_world_memory(app)
        return run_threshold_calibration(app)

    report = _persist_report("threshold_calibration", _with_temp_issue_memory(runner))
    if write_profile:
        store = IssueMemoryStore.from_env()
        store.initialize()
        profile_payload = {
            key: report[key]
            for key in (
                "version",
                "generated_at",
                "global",
                "families",
                "metrics",
                "datasets",
            )
            if key in report
        }
        store.settings.calibration_profile_path.write_text(
            json.dumps(profile_payload, indent=2, ensure_ascii=False, sort_keys=True),
            encoding="utf-8",
        )
        report["calibration_profile_path"] = str(
            store.settings.calibration_profile_path
        )
    print(json.dumps(report, indent=2, ensure_ascii=False))


def _render_dashboard_html(payload: dict[str, Any]) -> str:
    def block(title: str, content: Any) -> str:
        escaped = json.dumps(content, indent=2, ensure_ascii=False)
        return f"<section><h2>{title}</h2><pre>{escaped}</pre></section>"

    parts = [
        "<html><head><meta charset='utf-8'><title>Issue Memory Dashboard</title></head><body>",
        "<h1>Issue Memory Dashboard</h1>",
        block("metrics", payload.get("metrics", {})),
        block("calibration_profile", payload.get("calibration_profile", {})),
        block("reports", payload.get("reports", {})),
        "</body></html>",
    ]
    return "\n".join(parts)


def cmd_export_dashboard(output: str, fmt: str, window_days: int) -> None:
    store = IssueMemoryStore.from_env()
    store.initialize()
    payload = {
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "hostname": Settings.from_env().hostname,
        "metrics": store.metrics_summary(window_days=window_days),
        "calibration_profile": store.load_calibration_profile(),
        "reports": {
            item["name"]: store.load_saved_report(item["name"]) or item
            for item in store.list_saved_reports()[:8]
        },
    }
    output_path = Path(output).expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if fmt == "html":
        output_path.write_text(_render_dashboard_html(payload), encoding="utf-8")
    else:
        output_path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True),
            encoding="utf-8",
        )
    print(
        json.dumps({"status": "ok", "format": fmt, "path": str(output_path)}, indent=2)
    )


def cmd_analyze_feature_importance() -> None:
    store = IssueMemoryStore.from_env()
    store.initialize()
    stats = store.query_feature_outcome_stats()
    if not stats:
        print(
            json.dumps(
                {
                    "status": "ok",
                    "message": "No feature-outcome data yet. Submit feedback to populate.",
                },
                indent=2,
            )
        )
        return
    print(
        json.dumps(
            {"status": "ok", "feature_count": len(stats), "stats": stats},
            indent=2,
            default=str,
        )
    )


def cmd_sweep_implicit(timeout_minutes: int | None, limit: int) -> None:
    """Sweep retrieval events without feedback as implicit_ignore (Phase 2.1)."""
    store = IssueMemoryStore.from_env()
    store.initialize()
    results = store.sweep_implicit_rejections(
        timeout_minutes=timeout_minutes,
        limit=limit,
    )
    print(
        json.dumps(
            {"status": "ok", "swept": len(results), "events": results},
            indent=2,
            default=str,
        )
    )


# ------------------------------------------------------------------
# Phase 3.3 — A/B experiment commands
# ------------------------------------------------------------------


def cmd_create_experiment(
    *,
    experiment_id: str,
    name: str,
    description: str,
    traffic_fraction: float,
) -> None:
    store = IssueMemoryStore.from_env()
    store.initialize()
    result = store.create_experiment(
        experiment_id=experiment_id,
        name=name,
        description=description,
        traffic_fraction=traffic_fraction,
    )
    print(json.dumps(result, indent=2))


def cmd_update_experiment(experiment_id: str, status: str) -> None:
    store = IssueMemoryStore.from_env()
    store.initialize()
    result = store.update_experiment_status(experiment_id, status)
    print(json.dumps(result, indent=2))


def cmd_analyze_experiment(experiment_id: str) -> None:
    store = IssueMemoryStore.from_env()
    store.initialize()
    result = store.analyze_experiment(experiment_id)
    print(json.dumps(result, indent=2))


# ------------------------------------------------------------------
# Phase 3.4 — Auto weight calibration
# ------------------------------------------------------------------


def cmd_calibrate_weights(
    *, error_family: str = "", write_profile: bool = False
) -> None:
    """Calibrate ranking weights from feature_outcome_log data."""
    from .learning.weight_calibration import compute_optimal_weights
    from .retrieval.ranker import HeuristicRanker

    store = IssueMemoryStore.from_env()
    store.initialize()
    matrix = store.query_feature_outcome_matrix(error_family=error_family)
    if matrix.get("skipped"):
        print(
            json.dumps(
                {
                    "status": "skipped",
                    "reason": matrix.get("reason", "insufficient data"),
                },
                indent=2,
            )
        )
        return

    result = compute_optimal_weights(
        samples=matrix["samples"],
        feature_names=matrix["feature_names"],
        base_weights=dict(HeuristicRanker.DEFAULT_WEIGHTS),
    )
    result["status"] = "ok"
    if error_family:
        result["error_family"] = error_family

    if write_profile and result.get("weight_overrides"):
        from datetime import timezone

        profile = store.load_calibration_profile() or {}
        profile.setdefault("version", 1)
        profile["generated_at"] = datetime.now(tz=timezone.utc).isoformat()
        overrides = profile.setdefault("weight_overrides", {})
        family_key = error_family or "__global__"
        overrides[family_key] = result["weight_overrides"]
        store.settings.calibration_profile_path.write_text(
            json.dumps(profile, indent=2, ensure_ascii=False, sort_keys=True),
            encoding="utf-8",
        )
        result["calibration_profile_path"] = str(
            store.settings.calibration_profile_path
        )

    print(json.dumps(result, indent=2))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Maintenance commands for codex issue memory."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("init-db")
    subparsers.add_parser("migrate-v2")
    subparsers.add_parser("schema-version")
    subparsers.add_parser("backup")
    list_backups = subparsers.add_parser("list-backups")
    list_backups.add_argument("--limit", type=int, default=20)
    verify_backup = subparsers.add_parser("verify-backup")
    verify_backup.add_argument("path")
    restore_backup = subparsers.add_parser("restore-backup")
    restore_backup.add_argument("path")
    restore_backup.add_argument("--no-safety-backup", action="store_true")
    metrics = subparsers.add_parser("metrics")
    metrics.add_argument("--window-days", type=int, default=30)
    subparsers.add_parser("server-status")
    recommended = subparsers.add_parser("recommended-config")
    recommended.add_argument(
        "--mode", choices=("shadow", "active", "single"), default="shadow"
    )
    recommended.add_argument(
        "--format", choices=("json", "toml", "env"), default="json"
    )
    recommended.add_argument("--max-instances", type=int, default=0)
    doctor = subparsers.add_parser("doctor")
    doctor.add_argument(
        "--mode", choices=("shadow", "active", "single"), default="shadow"
    )
    doctor.add_argument("--max-instances", type=int, default=0)
    doctor.add_argument("--codex-home", default=None)
    prune = subparsers.add_parser("prune-retention")
    prune.add_argument("--telemetry-days", type=int, default=90)
    prune.add_argument("--review-days", type=int, default=120)
    review_queue = subparsers.add_parser("review-queue")
    review_queue.add_argument("--status", default="pending")
    review_queue.add_argument("--limit", type=int, default=20)
    resolve_review = subparsers.add_parser("resolve-review")
    resolve_review.add_argument("review_id", type=int)
    resolve_review.add_argument("decision")
    resolve_review.add_argument("--note", default="")
    subparsers.add_parser("smoke")
    subparsers.add_parser("smoke-learning")
    subparsers.add_parser("benchmark-user-domains")
    subparsers.add_parser("benchmark-failure-taxonomy")
    subparsers.add_parser("runtime-diagnostics")
    e2e_reuse = subparsers.add_parser("e2e-mcp-reuse-harness")
    e2e_reuse.add_argument("--timeout", type=float, default=10.0)
    e2e_reuse.add_argument("--json", action="store_true")
    subparsers.add_parser("benchmark-dense-bandit")
    subparsers.add_parser("benchmark-real-world")
    subparsers.add_parser("benchmark-hard-negatives")
    subparsers.add_parser("benchmark-merge-stress")
    benchmark_gate = subparsers.add_parser("quality-gate-benchmarks")
    benchmark_gate.add_argument("--full", action="store_true")
    calibrate = subparsers.add_parser("calibrate-thresholds")
    calibrate.add_argument("--write-profile", action="store_true")
    calibrate.add_argument(
        "--from-feedback",
        action="store_true",
        help="Use real feedback data instead of benchmark cases",
    )
    dashboard = subparsers.add_parser("export-dashboard")
    dashboard.add_argument("--output", required=True)
    dashboard.add_argument("--format", choices=("json", "html"), default="json")
    dashboard.add_argument("--window-days", type=int, default=30)
    subparsers.add_parser("analyze-feature-importance")
    sweep_impl = subparsers.add_parser("sweep-implicit")
    sweep_impl.add_argument("--timeout-minutes", type=int, default=None)
    sweep_impl.add_argument("--limit", type=int, default=500)
    # Phase 3.3 — A/B experiment commands
    create_exp = subparsers.add_parser("create-experiment")
    create_exp.add_argument("--id", required=True, dest="experiment_id")
    create_exp.add_argument("--name", required=True)
    create_exp.add_argument("--description", default="")
    create_exp.add_argument("--traffic-fraction", type=float, default=0.5)
    update_exp = subparsers.add_parser("update-experiment")
    update_exp.add_argument("experiment_id")
    update_exp.add_argument(
        "status", choices=("draft", "running", "paused", "completed", "cancelled")
    )
    analyze_exp = subparsers.add_parser("analyze-experiment")
    analyze_exp.add_argument("experiment_id")
    # Phase 3.4 — Auto weight calibration
    cal_weights = subparsers.add_parser("calibrate-weights")
    cal_weights.add_argument("--error-family", default="")
    cal_weights.add_argument("--write-profile", action="store_true")
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if args.command == "init-db":
        cmd_init_db()
    elif args.command == "migrate-v2":
        cmd_migrate_v2()
    elif args.command == "schema-version":
        cmd_schema_version()
    elif args.command == "backup":
        cmd_backup()
    elif args.command == "list-backups":
        cmd_list_backups(limit=args.limit)
    elif args.command == "verify-backup":
        cmd_verify_backup(args.path)
    elif args.command == "restore-backup":
        cmd_restore_backup(args.path, create_safety_backup=not args.no_safety_backup)
    elif args.command == "metrics":
        cmd_metrics(window_days=args.window_days)
    elif args.command == "server-status":
        cmd_server_status()
    elif args.command == "recommended-config":
        cmd_recommended_config(
            mode=args.mode, fmt=args.format, max_instances=args.max_instances
        )
    elif args.command == "doctor":
        cmd_doctor(
            mode=args.mode, max_instances=args.max_instances, codex_home=args.codex_home
        )
    elif args.command == "prune-retention":
        cmd_prune_retention(
            telemetry_days=args.telemetry_days, review_days=args.review_days
        )
    elif args.command == "review-queue":
        cmd_review_queue(status=args.status, limit=args.limit)
    elif args.command == "resolve-review":
        cmd_resolve_review(
            review_id=args.review_id, decision=args.decision, note=args.note
        )
    elif args.command == "smoke":
        cmd_smoke()
    elif args.command == "smoke-learning":
        cmd_smoke_learning()
    elif args.command == "benchmark-user-domains":
        cmd_benchmark_user_domains()
    elif args.command == "benchmark-failure-taxonomy":
        cmd_benchmark_failure_taxonomy()
    elif args.command == "runtime-diagnostics":
        cmd_runtime_diagnostics()
    elif args.command == "e2e-mcp-reuse-harness":
        cmd_e2e_mcp_reuse_harness(timeout=args.timeout, json_output=bool(args.json))
    elif args.command == "benchmark-dense-bandit":
        cmd_benchmark_dense_bandit()
    elif args.command == "benchmark-real-world":
        cmd_benchmark_real_world()
    elif args.command == "benchmark-hard-negatives":
        cmd_benchmark_hard_negatives()
    elif args.command == "benchmark-merge-stress":
        cmd_benchmark_merge_stress()
    elif args.command == "quality-gate-benchmarks":
        cmd_quality_gate_benchmarks(full=bool(args.full))
    elif args.command == "calibrate-thresholds":
        cmd_calibrate_thresholds(
            write_profile=bool(args.write_profile),
            from_feedback=bool(args.from_feedback),
        )
    elif args.command == "export-dashboard":
        cmd_export_dashboard(
            output=args.output, fmt=args.format, window_days=args.window_days
        )
    elif args.command == "analyze-feature-importance":
        cmd_analyze_feature_importance()
    elif args.command == "sweep-implicit":
        cmd_sweep_implicit(timeout_minutes=args.timeout_minutes, limit=args.limit)
    elif args.command == "create-experiment":
        cmd_create_experiment(
            experiment_id=args.experiment_id,
            name=args.name,
            description=args.description,
            traffic_fraction=args.traffic_fraction,
        )
    elif args.command == "update-experiment":
        cmd_update_experiment(args.experiment_id, args.status)
    elif args.command == "analyze-experiment":
        cmd_analyze_experiment(args.experiment_id)
    elif args.command == "calibrate-weights":
        cmd_calibrate_weights(
            error_family=args.error_family, write_profile=bool(args.write_profile)
        )
    else:
        raise SystemExit(f"Unknown command: {args.command}")


if __name__ == "__main__":
    main()
