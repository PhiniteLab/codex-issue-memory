from __future__ import annotations

import json
import re
from typing import Any

_SECRET_KEY_RE = re.compile(
    r"(?:^|[_\-\s])(?:pass(?:word)?|secret|token|api[_\-]?key|client[_\-]?secret|authorization|auth[_\-]?token|credential|private[_\-]?key)(?:$|[_\-\s])",
    re.IGNORECASE,
)

_SECRET_VALUE_RE = re.compile(
    r"^(?:sk-[A-Za-z0-9]{8,}|ghp_[A-Za-z0-9]{12,}|xox[baprs]-[A-Za-z0-9\-]{10,}|AKIA[0-9A-Z]{8,}|AIza[0-9A-Za-z\-_]{20,}|eyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+)$"
)

_TEXT_REPLACEMENTS: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.IGNORECASE | re.DOTALL),
        "[REDACTED_PRIVATE_KEY]",
    ),
    (re.compile(r"(?i)(\bbearer\s+)([A-Za-z0-9\-\._~\+/=]+)"), r"\1[REDACTED]"),
    (re.compile(r"(?i)(\bauthorization\s*[:=]\s*)([^\s,;]+)"), r"\1[REDACTED]"),
    (
        re.compile(
            r"(?i)(\b(?:api[_\-]?key|access[_\-]?token|refresh[_\-]?token|client[_\-]?secret|password|passwd|secret)\b\s*[:=]\s*)([^\s,;]+)"
        ),
        r"\1[REDACTED]",
    ),
]


def _truncate_text(text: str, *, max_chars: int) -> str:
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    omitted = len(text) - max_chars
    suffix = f" … [truncated {omitted} chars]"
    keep = max(max_chars - len(suffix), 0)
    return text[:keep].rstrip() + suffix


def _looks_secret_key(key: str) -> bool:
    return bool(_SECRET_KEY_RE.search(key.strip()))


def _looks_secret_value(value: str) -> bool:
    candidate = value.strip()
    if not candidate:
        return False
    if _SECRET_VALUE_RE.match(candidate):
        return True
    if len(candidate) >= 24 and re.fullmatch(r"[A-Za-z0-9_\-]{24,}", candidate):
        return True
    return False


def _redact_text(text: str) -> str:
    redacted = text
    for pattern, replacement in _TEXT_REPLACEMENTS:
        redacted = pattern.sub(replacement, redacted)
    return redacted


def _redact_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _redact_by_key(str(key), subvalue) for key, subvalue in value.items()}
    if isinstance(value, list):
        return [_redact_value(item) for item in value]
    if isinstance(value, str):
        redacted = _redact_text(value)
        if _looks_secret_value(redacted):
            return "[REDACTED]"
        return redacted
    return value


def _redact_by_key(key: str, value: Any) -> Any:
    if _looks_secret_key(key):
        return "[REDACTED]"
    return _redact_value(value)


def sanitize_text(text: str, *, enabled: bool = True, max_chars: int = 0) -> str:
    normalized = str(text or "")
    if not enabled:
        return _truncate_text(normalized, max_chars=max_chars)
    redacted = _redact_text(normalized)
    return _truncate_text(redacted, max_chars=max_chars)


def sanitize_json_text(text: str, *, enabled: bool = True, max_chars: int = 0) -> str:
    normalized = str(text or "").strip()
    if not enabled:
        return _truncate_text(normalized, max_chars=max_chars)
    if not normalized:
        return normalized
    try:
        payload = json.loads(normalized)
    except (TypeError, ValueError, json.JSONDecodeError):
        return sanitize_text(normalized, enabled=enabled, max_chars=max_chars)
    redacted = _redact_value(payload)
    return _truncate_text(json.dumps(redacted, ensure_ascii=False, sort_keys=True), max_chars=max_chars)


def sanitize_mapping(value: dict[str, Any], *, enabled: bool = True) -> dict[str, Any]:
    if not enabled:
        return dict(value)
    return _redact_value(dict(value))
