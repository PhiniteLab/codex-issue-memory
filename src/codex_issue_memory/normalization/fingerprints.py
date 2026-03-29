from __future__ import annotations

from hashlib import sha256
import json
from pathlib import PurePosixPath
from typing import Any

from .text import normalize_text, tokenize

_DIGEST_LEN = 16


def _stable_digest(*parts: str) -> str:
    material = '||'.join(part for part in parts if part)
    if not material:
        return ''
    return sha256(material.encode('utf-8')).hexdigest()[:_DIGEST_LEN]


def _canonical_jsonish(value: str | dict[str, Any] | list[Any] | None) -> str:
    if value is None:
        return ''
    if isinstance(value, (dict, list)):
        return json.dumps(value, sort_keys=True, separators=(',', ':'))
    text = str(value).strip()
    if not text:
        return ''
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return normalize_text(text)
    return json.dumps(parsed, sort_keys=True, separators=(',', ':'))


def make_command_signature(command: str) -> str:
    if not command.strip():
        return ''
    tokens = tokenize(command, max_tokens=8)
    prefix = '-'.join(tokens[:4]) or 'command'
    return f'cmd:{prefix}:{_stable_digest(normalize_text(command))}'


def make_path_signature(file_path: str) -> str:
    raw = file_path.strip().replace('\\', '/')
    if not raw:
        return ''
    normalized = normalize_text(raw)
    posix = PurePosixPath(raw.lower())
    tail = '/'.join(part for part in posix.parts[-3:] if part) or posix.name or 'path'
    return f'path:{tail}:{_stable_digest(normalized)}'


def make_stack_signature(stack_excerpt: str) -> str:
    if not stack_excerpt.strip():
        return ''
    canonical_stack = stack_excerpt.replace('\\', '/')
    lines = [normalize_text(line) for line in canonical_stack.splitlines() if line.strip()]
    salient = '\n'.join(lines[-8:])
    prefix = '-'.join(tokenize(salient, max_tokens=5)) or 'stack'
    return f'stk:{prefix}:{_stable_digest(salient)}'


def make_repo_fingerprint(repo_name: str = '', git_commit: str = '') -> str:
    canonical_repo = normalize_text(repo_name)
    canonical_commit = normalize_text(git_commit)
    if not canonical_repo and not canonical_commit:
        return ''
    label = '-'.join(tokenize(repo_name, max_tokens=3)) or 'repo'
    if canonical_commit:
        label = f'{label}:{canonical_commit[:12]}'
    return f'repo:{label}:{_stable_digest(canonical_repo, canonical_commit)}'


def make_env_fingerprint(
    env_json: str | dict[str, Any] | list[Any] | None,
    *,
    command: str = '',
    file_path: str = '',
    repo_name: str = '',
    git_commit: str = '',
) -> str:
    canonical_env = _canonical_jsonish(env_json)
    command_sig = make_command_signature(command)
    path_sig = make_path_signature(file_path)
    repo_sig = make_repo_fingerprint(repo_name=repo_name, git_commit=git_commit)
    if not any((canonical_env, command_sig, path_sig, repo_sig)):
        return ''
    label = '-'.join(tokenize(canonical_env, max_tokens=3)) or 'env'
    return f'env:{label}:{_stable_digest(canonical_env, command_sig, path_sig, repo_sig)}'
