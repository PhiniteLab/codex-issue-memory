from __future__ import annotations

from pathlib import Path
import re
from typing import Iterable

STOPWORDS = {
    "the", "and", "for", "with", "that", "this", "from", "into", "your", "when",
    "then", "than", "while", "were", "have", "has", "had", "there", "their", "they",
    "will", "would", "should", "could", "must", "been", "being", "after", "before",
    "during", "cannot", "cant", "does", "doesnt", "did", "didnt", "not", "error",
    "exception", "traceback", "line", "file", "path", "module", "failed", "failure",
    "because", "where", "what", "which", "about", "call", "main", "python", "true",
    "false", "none", "null", "stderr", "stdout", "test", "tests", "run",
}

PATH_RE = re.compile(r'(?:(?:[A-Za-z]:)?[\\/][^\s:;,\'"\)\]]+)')
HEX_RE = re.compile(r'0x[a-f0-9]+', re.IGNORECASE)
NUM_RE = re.compile(r'\b\d+\b')
SPACE_RE = re.compile(r'\s+')
EXCEPTION_RE = re.compile(
    r'\b([A-Za-z_][A-Za-z0-9_]*(?:Error|Exception|Warning))\b',
    re.IGNORECASE,
)


def _path_replacer(match: re.Match[str]) -> str:
    raw = match.group(0).strip('"\',()[]{}')
    normalized = raw.replace('\\', '/')
    name = Path(normalized).name or 'path'
    return f' path_{name.lower()} '


def normalize_text(text: str) -> str:
    """Normalize free-form text into a more stable lexical representation."""
    if not text:
        return ''
    text = text.replace('\\n', '\n').replace('\\t', '\t')
    text = PATH_RE.sub(_path_replacer, text)
    text = HEX_RE.sub(' __HEX__ ', text)
    text = NUM_RE.sub(' __NUM__ ', text)
    text = text.lower()
    text = re.sub(r'[^a-z0-9_./\-\s]', ' ', text)
    text = SPACE_RE.sub(' ', text).strip()
    return text


def tokenize(text: str, max_tokens: int = 64) -> list[str]:
    """Tokenize text after normalization, preserving order while deduplicating."""
    if not text:
        return []

    tokens: list[str] = []
    for token in normalize_text(text).split():
        if len(token) < 2:
            continue
        if token in STOPWORDS:
            continue
        tokens.append(token)

    deduped: list[str] = []
    seen: set[str] = set()
    for token in tokens:
        if token in seen:
            continue
        deduped.append(token)
        seen.add(token)
        if len(deduped) >= max_tokens:
            break
    return deduped


def comma_join(items: Iterable[str]) -> str:
    return ','.join(sorted(dict.fromkeys(x.strip() for x in items if x.strip())))


def parse_tag_string(tags: str) -> list[str]:
    if not tags:
        return []
    return [item.strip() for item in re.split(r'[,\n;]', tags) if item.strip()]


def canonicalize_exception_name(name: str) -> str:
    """Collapse exception spellings into a lower-case canonical token."""
    return re.sub(r'[^a-z0-9_]+', '', name.strip().lower())


def canonicalize_exception_type(name: str) -> str:
    return canonicalize_exception_name(name)


def canonicalize_exception_types(names: Iterable[str]) -> list[str]:
    canonical: list[str] = []
    seen: set[str] = set()
    for name in names:
        token = canonicalize_exception_name(str(name))
        if not token or token in seen:
            continue
        canonical.append(token)
        seen.add(token)
    return canonical


def extract_exception_types(text: str) -> list[str]:
    """Extract exception-like class names in canonical lower-case form."""
    if not text:
        return []
    matches = [m.group(1) for m in EXCEPTION_RE.finditer(text)]
    return sorted(canonicalize_exception_types(matches))
