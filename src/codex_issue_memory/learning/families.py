"""Strategy family hierarchy for hierarchical Bayesian shrinkage (Phase 3.2).

Strategies are grouped into families so that low-evidence strategies can
borrow strength from siblings with more data.
"""
from __future__ import annotations


STRATEGY_FAMILIES: dict[str, list[str]] = {
    "dependency_management": [
        "install_missing_dependency",
        "repair_package_layout",
        "optional_import_guard",
    ],
    "path_resolution": [
        "resolve_from___file__",
        "discover_repo_root",
        "move_path_to_config",
    ],
    "environment_setup": [
        "fix_interpreter_or_venv",
        "rehome_optimizer_state",
    ],
    "tensor_correctness": [
        "boundary_cast_float32",
        "assert_shape_contract",
        "move_batch_to_device_boundary",
    ],
    "validation": [
        "add_preflight_validation",
    ],
}

# Inverted lookup: strategy_key → family_key
_STRATEGY_TO_FAMILY: dict[str, str] = {}
for _fam_key, _members in STRATEGY_FAMILIES.items():
    for _sk in _members:
        _STRATEGY_TO_FAMILY[_sk] = _fam_key


def resolve_strategy_family(strategy_key: str) -> str:
    """Return the family key for *strategy_key*, or empty string if unknown."""
    return _STRATEGY_TO_FAMILY.get(strategy_key.strip(), "")


__all__ = [
    "STRATEGY_FAMILIES",
    "resolve_strategy_family",
]
