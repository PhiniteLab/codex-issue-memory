from __future__ import annotations

from typing import Iterable

from .text import normalize_text

_STRATEGY_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("resolve_from___file__", ("__file__", "relative to __file__", "avoid cwd", "cwd dependency", "resolve path relative")),
    ("discover_repo_root", ("repo root", "project root", "git rev-parse", "find root", "repository root")),
    ("move_path_to_config", ("move path to config", "configure path", "settings file", "config path")),
    ("fix_interpreter_or_venv", ("virtualenv", "venv", "wrong interpreter", "pythonpath", "interpreter mismatch")),
    ("install_missing_dependency", ("pip install", "poetry add", "module not found", "no module named", "missing dependency")),
    ("repair_package_layout", ("package layout", "__init__", "editable install", "python -m", "import path")),
    ("add_preflight_validation", ("fail-fast", "preflight", "validate", "validation", "assert key", "missing key")),
    ("boundary_cast_float32", ("float32", "cast", "dtype mismatch", "astype", ".float()")),
    ("assert_shape_contract", ("shape mismatch", "size mismatch", "dimension mismatch", "shape contract", "unexpected shape")),
    ("move_batch_to_device_boundary", ("same device", "move to device", "cuda", "tensor device", "batch to device")),
    ("rehome_optimizer_state", ("optimizer state", "resume optimizer", "checkpoint resume", "optimizer resume")),
    ("optional_import_guard", ("optional import", "fallback import", "soft dependency", "try/except import")),
)

_PRIORITY = [name for name, _patterns in _STRATEGY_RULES]


def _combined_text(parts: Iterable[str]) -> str:
    return normalize_text("\n".join(part for part in parts if part))


def infer_strategy_hints(*texts: str) -> list[str]:
    """Infer reusable solution-style hints from natural language and fixes.

    The output is intentionally conservative: it should prefer stable, reusable
    strategy identifiers over exact fix text.
    """

    combined = _combined_text(texts)
    if not combined:
        return []

    hints: list[str] = []
    for strategy_key, patterns in _STRATEGY_RULES:
        if any(pattern in combined for pattern in patterns):
            hints.append(strategy_key)

    if not hints and "file not found" in combined and "cwd" in combined:
        hints.append("resolve_from___file__")
    if not hints and "no module named" in combined:
        hints.append("install_missing_dependency")
    if not hints and "same device" in combined:
        hints.append("move_batch_to_device_boundary")
    if not hints and "dtype" in combined:
        hints.append("boundary_cast_float32")

    deduped: list[str] = []
    seen: set[str] = set()
    for name in _PRIORITY:
        if name in hints and name not in seen:
            deduped.append(name)
            seen.add(name)
    return deduped


def derive_strategy_key(*texts: str, fallback: str = "general_reusable_fix") -> str:
    hints = infer_strategy_hints(*texts)
    return hints[0] if hints else fallback
