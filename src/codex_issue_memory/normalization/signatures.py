from __future__ import annotations

from hashlib import sha256
import re
from typing import Any, Iterable

from .fingerprints import (
    make_command_signature,
    make_env_fingerprint,
    make_path_signature,
    make_stack_signature,
)
from .text import tokenize


def _slug_component(text: str, *, fallback: str) -> str:
    value = re.sub(r'[^a-z0-9_\-]+', '-', text.lower()).strip('-')
    return value or fallback


def build_symptom_cluster(*, title: str = '', canonical_symptom: str = '', tags: Iterable[str] | None = None) -> str:
    signature_tokens = tokenize(' '.join([title, canonical_symptom, ' '.join(tags or [])]), max_tokens=8)
    return '-'.join(signature_tokens[:5]) if signature_tokens else 'generic'


def make_pattern_key(
    *,
    title: str,
    project_scope: str,
    error_family: str,
    root_cause_class: str,
    canonical_symptom: str,
    tags: list[str],
) -> str:
    """Stable key for the abstract issue pattern layer."""
    scope = _slug_component(project_scope, fallback='global')
    family = _slug_component(error_family, fallback='generic')
    root = _slug_component(root_cause_class, fallback='unknown')
    cluster = build_symptom_cluster(title=title, canonical_symptom=canonical_symptom, tags=tags)
    return f'{scope}|{family}|{root}|{cluster}'


def make_variant_key(
    *,
    pattern_key: str,
    command: str = '',
    file_path: str = '',
    stack_excerpt: str = '',
    env_json: str | dict[str, Any] | list[Any] | None = None,
    repo_name: str = '',
    git_commit: str = '',
) -> str:
    """Stable key for context-specific variants within one abstract pattern."""
    components = [
        make_command_signature(command),
        make_path_signature(file_path),
        make_stack_signature(stack_excerpt),
        make_env_fingerprint(
            env_json,
            command=command,
            file_path=file_path,
            repo_name=repo_name,
            git_commit=git_commit,
        ),
    ]
    non_empty = [item for item in components if item]
    if not non_empty:
        return f'{pattern_key}|variant:default'
    digest = sha256('||'.join([pattern_key, *non_empty]).encode('utf-8')).hexdigest()[:16]
    context_descriptor = '|'.join(non_empty)
    return f'{pattern_key}|variant:{digest}|{context_descriptor}'
