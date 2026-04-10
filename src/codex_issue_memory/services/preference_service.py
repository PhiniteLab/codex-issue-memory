from __future__ import annotations

from typing import Any

from ..normalization import (
    build_query_profile,
    derive_strategy_key,
    infer_strategy_hints,
    tokenize,
)
from ..storage import IssueMemoryStore


_STRATEGY_FAMILY_HINTS = {
    "resolve_from___file__": "path_resolution_error",
    "discover_repo_root": "path_resolution_error",
    "move_path_to_config": "path_resolution_error",
    "fix_interpreter_or_venv": "import_error",
    "install_missing_dependency": "import_error",
    "repair_package_layout": "import_error",
    "add_preflight_validation": "config_error",
    "boundary_cast_float32": "tensor_dtype_error",
    "assert_shape_contract": "tensor_shape_error",
    "move_batch_to_device_boundary": "tensor_device_error",
    "rehome_optimizer_state": "environment_error",
    "optional_import_guard": "import_error",
}


class PreferenceService:
    """Store prompt-driven strategy preferences without duplicating pattern memory."""

    def __init__(self, store: IssueMemoryStore) -> None:
        self.store = store

    @staticmethod
    def _resolve_scope(user_scope: str, repo_name: str) -> tuple[str, str]:
        normalized_user_scope = user_scope.strip()
        normalized_repo_name = repo_name.strip()
        if normalized_user_scope:
            return "user", normalized_user_scope
        if normalized_repo_name:
            return "repo", normalized_repo_name
        return "global", ""

    @staticmethod
    def _bounded_weight(mode: str, weight: float | None) -> float:
        normalized_mode = mode.strip().lower() or "prefer"
        if normalized_mode not in {"prefer", "avoid"}:
            raise ValueError(f"Unsupported preference mode: {mode}")
        magnitude = (
            abs(float(weight))
            if weight is not None
            else (0.12 if normalized_mode == "prefer" else 0.14)
        )
        magnitude = min(max(magnitude, 0.02), 0.35)
        return magnitude if normalized_mode == "prefer" else -magnitude

    def set_rule(
        self,
        *,
        instruction: str,
        project_scope: str = "global",
        user_scope: str = "",
        repo_name: str = "",
        error_family: str = "auto",
        strategy_key: str = "auto",
        command: str = "",
        file_path: str = "",
        mode: str = "prefer",
        weight: float | None = None,
        source: str = "user_prompt",
    ) -> dict[str, Any]:
        normalized_instruction = instruction.strip()
        if not normalized_instruction:
            raise ValueError("Preference instruction must not be empty")

        resolved_user_scope = (
            user_scope.strip() or self.store.settings.default_user_scope
        )
        normalized_repo_name = repo_name.strip()
        normalized_project_scope = project_scope.strip() or "global"
        scope_type, scope_key = self._resolve_scope(
            resolved_user_scope, normalized_repo_name
        )

        profile = build_query_profile(
            error_text=normalized_instruction,
            context=normalized_instruction,
            command=command,
            file_path=file_path,
            repo_name=normalized_repo_name,
            project_scope=normalized_project_scope,
            user_scope=resolved_user_scope,
        )
        strategy_hints = infer_strategy_hints(
            normalized_instruction, command, file_path
        )
        resolved_strategy_key = (
            strategy_key.strip()
            if strategy_key and strategy_key != "auto"
            else derive_strategy_key(normalized_instruction, command, file_path)
        )
        resolved_error_family = (
            error_family.strip()
            if error_family and error_family != "auto"
            else profile.error_family
        )
        if resolved_error_family == "generic_runtime_error":
            resolved_error_family = _STRATEGY_FAMILY_HINTS.get(
                resolved_strategy_key, ""
            )

        payload = {
            "scope_type": scope_type,
            "scope_key": scope_key,
            "project_scope": normalized_project_scope,
            "repo_name": normalized_repo_name,
            "error_family": resolved_error_family,
            "strategy_key": resolved_strategy_key,
            "weight": self._bounded_weight(mode, weight),
            "instruction": normalized_instruction,
            "condition": {
                "repo_name": normalized_repo_name,
                "command_tokens": tokenize(command, max_tokens=8),
                "path_tokens": tokenize(file_path.replace("\\", "/"), max_tokens=8),
                "strategy_hints": strategy_hints,
                "mode": mode.strip().lower() or "prefer",
            },
            "source": source.strip() or "user_prompt",
            "active": True,
        }
        stored = self.store.upsert_preference_rule(payload)
        return {
            "created": True,
            "rule": stored,
            "derived": {
                "scope_type": scope_type,
                "scope_key": scope_key,
                "project_scope": normalized_project_scope,
                "repo_name": normalized_repo_name,
                "user_scope": resolved_user_scope,
                "error_family": resolved_error_family or "",
                "strategy_key": resolved_strategy_key,
                "strategy_hints": strategy_hints,
                "mode": mode.strip().lower() or "prefer",
            },
            "note": (
                "Preference stored as a ranking overlay. It biases candidate selection and guardrails "
                "without duplicating issue patterns or variants."
            ),
        }

    def list_rules(
        self,
        *,
        scope_type: str = "",
        scope_key: str = "",
        project_scope: str = "",
        repo_name: str = "",
        active_only: bool = True,
        limit: int = 20,
    ) -> dict[str, Any]:
        rows = self.store.list_preference_rules(
            scope_type=scope_type,
            scope_key=scope_key,
            project_scope=project_scope,
            repo_name=repo_name,
            active_only=active_only,
            limit=limit,
        )
        compact = [
            {
                "rule_id": int(row["id"]),
                "scope_type": str(row["scope_type"]),
                "scope_key": str(row["scope_key"]),
                "project_scope": str(row["project_scope"]),
                "repo_name": str(row.get("repo_name", "")),
                "error_family": str(row.get("error_family", "")),
                "strategy_key": str(row.get("strategy_key", "")),
                "weight": round(float(row.get("weight", 0.0)), 4),
                "instruction": str(row.get("instruction", "")),
                "source": str(row.get("source", "")),
                "active": bool(row.get("active", True)),
                "updated_at": str(row.get("updated_at", "")),
            }
            for row in rows
        ]
        return {"rules": compact}
