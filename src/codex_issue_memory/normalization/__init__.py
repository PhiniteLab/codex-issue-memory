"""Normalization and fingerprint utilities.

This package replaces the old single-file ``normalization.py`` module while
keeping the public import surface stable for existing callers.
"""

from __future__ import annotations

from .classify import classify_from_text
from .entities import compare_entity_slots, extract_entity_slots
from .fingerprints import (
    make_command_signature,
    make_env_fingerprint,
    make_path_signature,
    make_repo_fingerprint,
    make_stack_signature,
)
from .query_profile import build_query_profile
from .strategies import derive_strategy_key, infer_strategy_hints
from .signatures import build_symptom_cluster, make_pattern_key, make_variant_key
from .text import (
    STOPWORDS,
    canonicalize_exception_name,
    canonicalize_exception_type,
    canonicalize_exception_types,
    comma_join,
    extract_exception_types,
    normalize_text,
    parse_tag_string,
    tokenize,
)

_classify_from_text = classify_from_text

__all__ = [
    'STOPWORDS',
    '_classify_from_text',
    'build_query_profile',
    'build_symptom_cluster',
    'canonicalize_exception_name',
    'canonicalize_exception_type',
    'canonicalize_exception_types',
    'classify_from_text',
    'comma_join',
    'compare_entity_slots',
    'derive_strategy_key',
    'extract_entity_slots',
    'extract_exception_types',
    'infer_strategy_hints',
    'make_command_signature',
    'make_env_fingerprint',
    'make_path_signature',
    'make_pattern_key',
    'make_repo_fingerprint',
    'make_stack_signature',
    'make_variant_key',
    'normalize_text',
    'parse_tag_string',
    'tokenize',
]
