from __future__ import annotations

from pathlib import PurePosixPath

from ..models import QueryProfile
from .classify import classify_from_text
from .entities import extract_entity_slots
from .fingerprints import (
    make_command_signature,
    make_env_fingerprint,
    make_path_signature,
    make_repo_fingerprint,
    make_stack_signature,
)
from .strategies import infer_strategy_hints
from .text import extract_exception_types, normalize_text, tokenize


def _dedupe_preserve_order(tokens: list[str], *, max_tokens: int) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for token in tokens:
        if not token or token in seen:
            continue
        deduped.append(token)
        seen.add(token)
        if len(deduped) >= max_tokens:
            break
    return deduped


def _expand_command_tokens(command: str) -> list[str]:
    base_tokens = tokenize(command, max_tokens=20)
    expanded: list[str] = []
    for token in base_tokens:
        expanded.append(token)
        stripped = token.lstrip("-")
        if stripped and stripped != token:
            expanded.append(stripped)
        if "/" in stripped:
            expanded.append(stripped.rsplit("/", 1)[-1])
    return _dedupe_preserve_order(expanded, max_tokens=24)


def _expand_path_tokens(file_path: str) -> list[str]:
    raw = file_path.strip().replace("\\", "/")
    expanded = list(tokenize(raw, max_tokens=12))
    if raw:
        posix = PurePosixPath(raw)
        expanded.extend(part.lower() for part in posix.parts if part not in {"/", ".", ".."})
        if posix.name:
            expanded.append(posix.name.lower())
    return _dedupe_preserve_order(expanded, max_tokens=16)


def build_query_profile(
    error_text: str,
    *,
    context: str = "",
    command: str = "",
    file_path: str = "",
    stack_excerpt: str = "",
    env_json: str | dict[str, object] | list[object] | None = None,
    repo_name: str = "",
    git_commit: str = "",
    project_scope: str = "global",
    user_scope: str = "",
    hint_family: str | None = None,
    hint_root_cause: str | None = None,
    extra_tags: list[str] | None = None,
) -> QueryProfile:
    """Create a richer normalized profile for matching and future strategy routing."""

    raw_parts = [error_text, context, stack_excerpt, command, file_path]
    combined = "\n".join(part for part in raw_parts if part)
    normalized = normalize_text(combined)

    symptom_tokens = tokenize(error_text, max_tokens=32)
    context_tokens = tokenize(context, max_tokens=24)
    stack_tokens = tokenize(stack_excerpt, max_tokens=24)
    command_tokens = _expand_command_tokens(command)
    path_tokens = _expand_path_tokens(file_path)
    tokens = _dedupe_preserve_order(
        symptom_tokens + context_tokens + stack_tokens + command_tokens + path_tokens,
        max_tokens=64,
    )

    exception_source = "\n".join(part for part in [error_text, context, stack_excerpt] if part)
    exception_types = extract_exception_types(exception_source)
    family, root, tags, evidence = classify_from_text(normalized)

    if hint_family and hint_family != "auto":
        family = hint_family
        evidence.append("hint-family")
    if hint_root_cause and hint_root_cause != "auto":
        root = hint_root_cause
        evidence.append("hint-root-cause")

    entity_slots = extract_entity_slots(
        error_text=error_text,
        context=context,
        command=command,
        file_path=file_path,
        stack_excerpt=stack_excerpt,
        env_json=env_json,
        repo_name=repo_name,
    )
    strategy_hints = infer_strategy_hints(error_text, context, command, file_path, stack_excerpt)

    normalized_extra_tags = [tag.strip() for tag in (extra_tags or []) if tag.strip()]
    all_tags = list(dict.fromkeys(tags + exception_types + normalized_extra_tags))

    return QueryProfile(
        raw_text=combined,
        normalized_text=normalized,
        tokens=tokens,
        exception_types=exception_types,
        error_family=family,
        root_cause_class=root,
        tags=all_tags,
        evidence=evidence,
        symptom_tokens=symptom_tokens,
        context_tokens=context_tokens + [token for token in stack_tokens if token not in context_tokens],
        command_tokens=command_tokens,
        path_tokens=path_tokens,
        stack_signature=make_stack_signature(stack_excerpt or error_text),
        env_fingerprint=make_env_fingerprint(
            env_json,
            command=command,
            file_path=file_path,
            repo_name=repo_name,
            git_commit=git_commit,
        ),
        repo_fingerprint=make_repo_fingerprint(repo_name=repo_name, git_commit=git_commit),
        repo_name=repo_name.strip(),
        project_scope=project_scope.strip() or "global",
        user_scope=user_scope.strip(),
        entity_slots=entity_slots,
        strategy_hints=strategy_hints,
        command_signature=make_command_signature(command),
        path_signature=make_path_signature(file_path),
    )
