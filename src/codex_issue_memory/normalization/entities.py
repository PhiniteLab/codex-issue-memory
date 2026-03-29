from __future__ import annotations

import re
from pathlib import PurePosixPath
from typing import Any

from .text import normalize_text, tokenize

_MODULE_PATTERNS = (
    re.compile(r"no module named ['\"]?([a-zA-Z0-9_.-]+)['\"]?", re.IGNORECASE),
    re.compile(r"cannot import name ['\"]?([a-zA-Z0-9_.-]+)['\"]? from ['\"]?([a-zA-Z0-9_.-]+)['\"]?", re.IGNORECASE),
)
_CONFIG_KEY_PATTERNS = (
    re.compile(r"missing (?:required )?(?:config )?key ['\"]?([a-zA-Z0-9_.-]+)['\"]?", re.IGNORECASE),
    re.compile(r"keyerror: ['\"]?([a-zA-Z0-9_.-]+)['\"]?", re.IGNORECASE),
)
_PATH_PATTERNS = (
    re.compile(r"((?:[a-zA-Z]:)?[\/][^\s:'\"]+|(?:\.?\.?/)?[a-zA-Z0-9_.-]+(?:/[a-zA-Z0-9_.-]+)+)"),
    re.compile(r"([a-zA-Z0-9_.-]+\.(?:py|yml|yaml|json|toml|pt|pth|ckpt|sqlite3|db|txt|csv))", re.IGNORECASE),
)
_DEVICE_PATTERN = re.compile(r"\b(cpu|cuda|mps|xpu)\b", re.IGNORECASE)
_DTYPE_PATTERN = re.compile(r"\b(float16|float32|float64|bfloat16|int8|int16|int32|int64|long|half|double)\b", re.IGNORECASE)
_SHAPE_PATTERN = re.compile(r"\(([0-9, x\s-]+)\)")
_ALGO_NAMES = {"ppo", "sac", "ddpg", "td3", "a2c", "a3c", "mpc", "hjb", "bellman", "dqn", "reinforce"}

_CRITICAL_SCALAR_KEYS = (
    "module_name",
    "import_target",
    "config_key",
    "anchor_file",
    "algo_name",
    "device_from",
    "device_to",
    "dtype_from",
    "dtype_to",
    "shape_expected",
    "shape_actual",
)
_SOFT_SCALAR_KEYS = (
    "repo_name",
    "missing_path",
    "anchor_path",
)
_LIST_KEYS = ("devices", "dtypes", "shapes", "algo_family")


def _first_path_candidate(*texts: str) -> str:
    for text in texts:
        if not text:
            continue
        for pattern in _PATH_PATTERNS:
            match = pattern.search(text.replace("\\", "/"))
            if match:
                return match.group(1).replace("\\", "/")
    return ""


def _normalize_path(value: str) -> str:
    raw = value.strip().replace("\\", "/")
    if not raw:
        return ""
    try:
        path = PurePosixPath(raw)
        return "/".join(part for part in path.parts if part not in {"/", "."})
    except Exception:
        return raw


def _dedupe_list(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        cleaned = value.strip().lower()
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        result.append(cleaned)
    return result


def extract_entity_slots(
    *,
    error_text: str,
    context: str = "",
    command: str = "",
    file_path: str = "",
    stack_excerpt: str = "",
    env_json: str | dict[str, Any] | list[Any] | None = None,
    repo_name: str = "",
) -> dict[str, Any]:
    """Extract conservative, family-agnostic entity slots for later merge guards.

    The extractor intentionally prefers low-recall / high-precision slots. These
    fields are meant to support future variant routing and conservative learning,
    not to be a complete semantic parser.
    """

    del env_json  # reserved for later structured extraction without changing API

    slots: dict[str, Any] = {}
    combined = "\n".join(part for part in [error_text, context, stack_excerpt, command, file_path] if part)
    normalized = normalize_text(combined)

    module_name = ""
    import_target = ""
    for pattern in _MODULE_PATTERNS:
        match = pattern.search(error_text) or pattern.search(context) or pattern.search(stack_excerpt)
        if not match:
            continue
        module_name = match.group(1).strip().lower()
        if pattern.groups >= 2 and match.lastindex and match.lastindex >= 2:
            import_target = match.group(2).strip().lower()
        break
    if module_name:
        slots["module_name"] = module_name
    if import_target:
        slots["import_target"] = import_target

    for pattern in _CONFIG_KEY_PATTERNS:
        match = pattern.search(combined)
        if match:
            slots["config_key"] = match.group(1).strip().lower()
            break

    missing_path = _normalize_path(_first_path_candidate(error_text, context, stack_excerpt))
    anchor_path = _normalize_path(file_path)
    if missing_path:
        slots["missing_path"] = missing_path
    if anchor_path:
        slots["anchor_path"] = anchor_path
        basename = PurePosixPath(anchor_path).name.lower()
        if basename:
            slots["anchor_file"] = basename

    devices = _dedupe_list(_DEVICE_PATTERN.findall(combined))
    if devices:
        slots["devices"] = devices[:3]
        if len(devices) >= 2:
            slots["device_from"] = devices[0]
            slots["device_to"] = devices[1]

    dtypes = _dedupe_list(_DTYPE_PATTERN.findall(combined))
    if dtypes:
        slots["dtypes"] = dtypes[:4]
        if len(dtypes) >= 2:
            slots["dtype_from"] = dtypes[0]
            slots["dtype_to"] = dtypes[1]

    shapes = []
    for match in _SHAPE_PATTERN.finditer(combined):
        shape = re.sub(r"\s+", "", match.group(1))
        if shape and any(ch.isdigit() for ch in shape):
            shapes.append(shape)
    shapes = _dedupe_list(shapes)
    if shapes:
        slots["shapes"] = shapes[:3]
        if len(shapes) >= 2:
            slots["shape_expected"] = shapes[0]
            slots["shape_actual"] = shapes[1]

    tokens = set(tokenize(normalized, max_tokens=96))
    algo_hits = [token for token in _dedupe_list(list(tokens)) if token in _ALGO_NAMES]
    if algo_hits:
        slots["algo_name"] = algo_hits[0]
        if len(algo_hits) > 1:
            slots["algo_family"] = algo_hits[:3]

    if repo_name.strip():
        slots["repo_name"] = repo_name.strip().lower()

    return slots


def _normalize_entity_value(value: Any) -> str:
    if isinstance(value, list):
        return " ".join(str(item).strip().lower() for item in value if str(item).strip())
    return str(value).strip().lower()


def compare_entity_slots(
    query_slots: dict[str, Any] | None,
    candidate_slots: dict[str, Any] | None,
) -> dict[str, Any]:
    """Return conservative compatibility signals between two entity-slot dictionaries."""

    left = query_slots if isinstance(query_slots, dict) else {}
    right = candidate_slots if isinstance(candidate_slots, dict) else {}
    if not left or not right:
        return {
            "match_score": 0.0,
            "conflict_penalty": 0.0,
            "matched_keys": [],
            "conflict_keys": [],
            "reasons": [],
        }

    matched_keys: list[str] = []
    conflict_keys: list[str] = []
    reasons: list[str] = []
    score = 0.0
    penalty = 0.0

    for key in _CRITICAL_SCALAR_KEYS:
        lval = _normalize_entity_value(left.get(key, ""))
        rval = _normalize_entity_value(right.get(key, ""))
        if not lval or not rval:
            continue
        if lval == rval:
            score += 0.18
            matched_keys.append(key)
            reasons.append(f"entity-match:{key}")
        else:
            penalty += 0.24
            conflict_keys.append(key)
            reasons.append(f"entity-conflict:{key}")

    for key in _SOFT_SCALAR_KEYS:
        lval = _normalize_entity_value(left.get(key, ""))
        rval = _normalize_entity_value(right.get(key, ""))
        if not lval or not rval:
            continue
        if lval == rval or lval.endswith(rval) or rval.endswith(lval):
            score += 0.10
            matched_keys.append(key)
            reasons.append(f"entity-match:{key}")
        else:
            penalty += 0.08
            conflict_keys.append(key)
            reasons.append(f"entity-conflict:{key}")

    for key in _LIST_KEYS:
        lset = {str(item).strip().lower() for item in left.get(key, []) if str(item).strip()} if isinstance(left.get(key), list) else set()
        rset = {str(item).strip().lower() for item in right.get(key, []) if str(item).strip()} if isinstance(right.get(key), list) else set()
        if not lset or not rset:
            continue
        overlap = lset & rset
        if overlap:
            score += min(len(overlap), 2) * 0.06
            matched_keys.append(key)
            reasons.append(f"entity-match:{key}")
        else:
            penalty += 0.10
            conflict_keys.append(key)
            reasons.append(f"entity-conflict:{key}")

    return {
        "match_score": round(min(score, 1.0), 6),
        "conflict_penalty": round(min(penalty, 1.0), 6),
        "matched_keys": matched_keys,
        "conflict_keys": conflict_keys,
        "reasons": reasons[:12],
    }


__all__ = ["extract_entity_slots", "compare_entity_slots"]
